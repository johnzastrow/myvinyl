"""myvinyl web application."""

import csv
import hmac
import io
import logging
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from . import charts, db, discogs
from .albums import (
    CONDITIONS,
    FORMATS,
    MAX_REVIEW,
    MAX_TRACK_NOTE,
    RATINGS,
    album_to_values,
    format_money,
    format_stars,
    parse_album,
    parse_identifier,
    parse_rating,
)
from .auth import LoginLimiter, verify_password
from .config import Settings
from .lookups import LookupQueue, RefreshScheduler

PKG_DIR = Path(__file__).parent
SESSION_MAX_AGE = 12 * 60 * 60  # 12 hours
MAX_QUERY_LEN = 200
REFRESH_BATCH = 20  # albums re-queued per hourly scheduler run
EASTERN = ZoneInfo("America/New_York")

log = logging.getLogger("myvinyl")

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self'; img-src 'self' https://i.discogs.com; "
        "script-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}


class NotAuthenticated(Exception):
    pass


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


async def checked_form(request: Request):
    """Read a POSTed form, rejecting it unless the CSRF token matches the session."""
    form = await request.form()
    sent = form.get("csrf")
    expected = request.session.get("csrf")
    if not expected or not isinstance(sent, str) or not hmac.compare_digest(sent, expected):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
    return form


def require_user(request: Request) -> None:
    if request.session.get("user") != "owner":
        raise NotAuthenticated()


def csv_safe(value) -> str:
    """Neutralize spreadsheet formula injection in exported cells."""
    text = "" if value is None else str(value)
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def eastern_time(timestamp: str | None) -> str:
    """Format a stored UTC timestamp ('YYYY-MM-DD HH:MM:SS') in US Eastern time."""
    if not timestamp:
        return ""
    try:
        utc = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return timestamp
    local = utc.astimezone(EASTERN)
    return f"{local:%b} {local.day}, {local:%Y} {local:%I:%M %p %Z}".replace(" 0", " ", 1)


def eastern_date(timestamp: str | None) -> str:
    text = eastern_time(timestamp)
    return text.rsplit(" ", 3)[0] if text else ""


def positive_int(raw) -> int | None:
    text = str(raw or "").strip()
    return int(text) if text.isascii() and text.isdigit() and 0 < int(text) < 2**31 else None


def create_app(
    settings: Settings,
    service: discogs.DiscogsService | None = None,
    inline_jobs: bool = False,
) -> FastAPI:
    """Build the app. Tests pass a fake `service` and inline_jobs=True (no threads)."""
    if service is None:
        client = discogs.DiscogsClient(settings.discogs_token)
        service = discogs.DiscogsService(client, has_token=bool(settings.discogs_token))
    db.init(settings.db_path)
    path = settings.db_path

    # --- Background work -------------------------------------------------------------

    def run_enrichment(album_id: int) -> None:
        """Discogs lookup for one album (runs on the lookup worker). Never raises."""
        album = db.get_album(path, album_id)
        if album is None:
            return  # deleted while queued
        pinned = album["discogs_release_id"] if album["pressing_locked"] else None
        try:
            result = service.enrich(album["artist"], album["title"], album["year"], pinned)
            if result is None:
                db.set_discogs_status(path, album_id, "none")
            else:
                db.save_enrichment(path, album_id, result)
        except discogs.LOOKUP_ERRORS as exc:
            log.warning("Discogs lookup failed for album %s: %s", album_id, exc)
            db.set_discogs_status(path, album_id, "error")
        except Exception:
            log.exception("unexpected error during Discogs lookup for album %s", album_id)
            db.set_discogs_status(path, album_id, "error")

    lookups = LookupQueue(run_enrichment, inline=inline_jobs)

    def queue_lookup(album_id: int) -> None:
        db.set_discogs_status(path, album_id, "pending")
        lookups.submit(album_id)

    def refresh_due() -> int:
        """Queue albums whose prices are older than the refresh interval."""
        if settings.refresh_days <= 0:
            return 0
        due = db.due_for_refresh(path, settings.refresh_days, REFRESH_BATCH)
        for album_id in due:
            queue_lookup(album_id)
        return len(due)

    scheduler = RefreshScheduler(refresh_due)
    import_state = {"state": "idle", "added": 0, "skipped": 0, "message": ""}
    import_lock = threading.Lock()

    def run_import(condition: str) -> None:
        """Copy the Discogs collection into myvinyl, skipping releases already here."""
        try:
            for item in service.collection():
                if db.album_for_release(path, item["release_id"]):
                    import_state["skipped"] += 1
                    continue
                data = {
                    "artist": item["artist"],
                    "title": item["title"],
                    "year": item["year"] if item["year"] and 1900 <= item["year"] <= 2100 else None,
                    "label": item["label"],
                    "format": item["format"],
                    "condition": condition,
                    "notes": "",
                    "value_cents": None,
                }
                album_id = db.create_album(
                    path, data, release_id=item["release_id"], rating=item["rating"]
                )
                queue_lookup(album_id)
                import_state["added"] += 1
            import_state["state"] = "done"
        except discogs.LOOKUP_ERRORS as exc:
            log.warning("Discogs import failed: %s", exc)
            import_state.update(state="error", message="Discogs could not be reached.")
        except Exception:
            log.exception("Discogs import failed")
            import_state.update(state="error", message="The import stopped unexpectedly.")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        lookups.start()
        if not inline_jobs:
            scheduler.start()
        yield
        scheduler.stop()
        lookups.stop()

    # --- App setup -------------------------------------------------------------------

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.refresh_due = refresh_due  # exposed for tests and manual triggering
    templates = Jinja2Templates(directory=PKG_DIR / "templates")
    templates.env.filters["money"] = format_money
    templates.env.filters["eastern"] = eastern_time
    templates.env.filters["eastern_date"] = eastern_date
    templates.env.filters["stars"] = format_stars
    limiter = LoginLimiter()

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="myvinyl_session",
        max_age=SESSION_MAX_AGE,
        same_site="lax",
        https_only=True,  # Secure flag; browsers still accept it on http://localhost
    )
    app.mount("/static", StaticFiles(directory=PKG_DIR / "static"), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return FileResponse(PKG_DIR / "static" / "favicon.ico")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(NotAuthenticated)
    async def to_login(request: Request, exc: NotAuthenticated):
        return RedirectResponse("/login", status_code=303)

    def render(request: Request, name: str, status_code: int = 200, **context):
        context["csrf"] = csrf_token(request)
        return templates.TemplateResponse(request, name, context, status_code=status_code)

    def album_form(request, album_id=None, values=None, errors=None, status_code=200, album=None):
        return render(
            request,
            "form.html",
            status_code=status_code,
            album_id=album_id,
            album=album,
            values=values or {"format": "LP", "condition": "VG+"},
            errors=errors or {},
            formats=FORMATS,
            conditions=CONDITIONS,
        )

    def album_or_404(album_id: int):
        album = db.get_album(path, album_id)
        if album is None:
            raise HTTPException(status_code=404, detail="Album not found.")
        return album

    def release_tracks(album_id: int) -> list[dict]:
        release, _ = db.get_discogs(path, album_id)
        return discogs.release_summary(release)["tracks"] if release else []

    # --- Public routes ---------------------------------------------------------------

    @app.get("/login")
    async def login_page(request: Request):
        if request.session.get("user") == "owner":
            return RedirectResponse("/", status_code=303)
        return render(request, "login.html")

    @app.post("/login")
    async def login(request: Request):
        form = await checked_form(request)
        client = request.client.host if request.client else "unknown"
        if limiter.blocked(client):
            log.warning("login blocked by rate limit: %s", client)
            return render(request, "login.html", 429, error="Too many attempts. Try again later.")

        password = form.get("password")
        if (
            isinstance(password, str)
            and len(password) <= 1024
            and verify_password(settings.password_hash, password)
        ):
            limiter.reset(client)
            request.session.clear()  # fresh session (and CSRF token) on login
            request.session["user"] = "owner"
            return RedirectResponse("/", status_code=303)

        limiter.record_failure(client)
        log.warning("failed login: %s", client)
        return render(request, "login.html", 401, error="Incorrect password.")

    # --- Authenticated routes --------------------------------------------------------

    router = APIRouter(dependencies=[Depends(require_user)])

    @router.post("/logout")
    async def logout(request: Request):
        await checked_form(request)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @router.get("/")
    async def index(request: Request):
        params = request.query_params
        q = params.get("q", "").strip()[:MAX_QUERY_LEN]
        sort = params.get("sort", "artist")
        sort = sort if sort in db.SORTS else "artist"
        direction = "desc" if params.get("dir") == "desc" else "asc"
        return render(
            request,
            "index.html",
            albums=db.list_albums(path, q, sort, direction),
            q=q,
            sort=sort,
            direction=direction,
            totals=db.totals(path),
            queued=lookups.pending(),
        )

    @router.get("/albums/new")
    async def new_album(request: Request):
        return album_form(request)

    @router.post("/albums")
    async def create_album(request: Request):
        form = await checked_form(request)
        identifier, id_error = parse_identifier(form.get("identifier"))
        release_id = positive_int(form.get("release_id"))
        raw = dict(form)

        if id_error:
            _, values, errors = parse_album(raw)
            values["identifier"] = str(form.get("identifier", ""))[:60]
            return album_form(
                request, values=values, errors={**errors, "identifier": id_error}, status_code=422
            )

        if identifier and release_id is None:
            # Exact match by barcode or catalog number.
            try:
                matches = await run_in_threadpool(service.find_by_identifier, identifier)
            except discogs.LOOKUP_ERRORS as exc:
                log.warning("Discogs identifier search failed: %s", exc)
                matches = None
            if not matches:
                _, values, _ = parse_album(raw)
                values["identifier"] = identifier
                message = (
                    "Discogs could not be reached. Try again, or leave this blank."
                    if matches is None
                    else "No vinyl release on Discogs has that barcode or catalog number."
                )
                return album_form(
                    request, values=values, errors={"identifier": message}, status_code=422
                )
            if len(matches) > 1:
                return render(
                    request, "choose.html", identifier=identifier, matches=matches, form=raw
                )
            match = matches[0]
            release_id = match["release_id"]
            # Fill only what you left blank; format comes from the release itself because
            # the form's format menu always has a value.
            for key in ("artist", "title", "label", "year"):
                if not str(raw.get(key, "")).strip() and match[key]:
                    raw[key] = str(match[key])
            raw["format"] = match["format"]

        data, values, errors = parse_album(raw)
        if errors:
            values["identifier"] = identifier
            return album_form(request, values=values, errors=errors, status_code=422)
        album_id = db.create_album(path, data, release_id=release_id, identifier=identifier)
        # Range and metadata are always looked up; the value only if you left it blank.
        queue_lookup(album_id)
        return RedirectResponse(f"/albums/{album_id}", status_code=303)

    @router.get("/albums/{album_id}")
    async def album_detail(request: Request, album_id: int):
        album = album_or_404(album_id)
        release, pressings = db.get_discogs(path, album_id)
        summary = discogs.release_summary(release) if release else None
        cover = ""
        tracks = []
        if summary:
            # Your pick if it is still among the release's images, else the default.
            uris = {i["uri"] for i in summary["images"]}
            cover = album["cover_uri"] if album["cover_uri"] in uris else summary["cover"]
            ratings = db.get_track_ratings(path, album_id)
            for t in summary["tracks"]:
                r = ratings.get(t["position"])
                tracks.append(
                    {**t, "rating": r["rating"] if r else None, "note": r["note"] if r else ""}
                )
        return render(
            request,
            "album.html",
            album=album,
            release=summary,
            cover=cover,
            tracks=tracks,
            pressings=pressings,
            chart=charts.value_history(db.get_history(path, album_id)),
            ratings=RATINGS,
        )

    @router.get("/albums/{album_id}/edit")
    async def edit_album(request: Request, album_id: int):
        album = album_or_404(album_id)
        return album_form(request, album_id=album_id, values=album_to_values(album), album=album)

    @router.post("/albums/{album_id}")
    async def update_album(request: Request, album_id: int):
        data, values, errors = parse_album(await checked_form(request))
        album = album_or_404(album_id)
        if errors:
            return album_form(request, album_id, values, errors, status_code=422, album=album)
        if data["value_cents"] is None:
            source = None  # cleared: the next Discogs lookup may fill it
        elif data["value_cents"] == album["value_cents"]:
            source = album["value_source"]
        else:
            source = "manual"  # you overrode the value; lookups won't replace it
        db.update_album(path, album_id, data, source)
        return RedirectResponse(f"/albums/{album_id}", status_code=303)

    @router.post("/albums/{album_id}/lookup")
    async def lookup_album(request: Request, album_id: int):
        await checked_form(request)
        album = album_or_404(album_id)
        if album["discogs_status"] != "pending":
            queue_lookup(album_id)
        return RedirectResponse(f"/albums/{album_id}", status_code=303)

    @router.post("/albums/{album_id}/pressing")
    async def choose_pressing(request: Request, album_id: int):
        """'This is my pressing': pin one of the checked pressings, or go back to auto."""
        form = await checked_form(request)
        album_or_404(album_id)
        if form.get("mode") == "auto":
            db.set_pressing(path, album_id, None)
        else:
            release_id = positive_int(form.get("release_id"))
            _, pressings = db.get_discogs(path, album_id)
            if release_id not in {p["release_id"] for p in pressings}:
                raise HTTPException(status_code=400, detail="Unknown pressing.")
            db.set_pressing(path, album_id, release_id)
        queue_lookup(album_id)
        return RedirectResponse(f"/albums/{album_id}", status_code=303)

    @router.post("/albums/{album_id}/cover")
    async def choose_cover(request: Request, album_id: int):
        form = await checked_form(request)
        album_or_404(album_id)
        release, _ = db.get_discogs(path, album_id)
        uri = form.get("uri")
        # Only accept one of the release's own image URLs, never an arbitrary URL.
        allowed = {i["uri"] for i in discogs.release_images(release or {})}
        if not isinstance(uri, str) or uri not in allowed:
            raise HTTPException(status_code=400, detail="Unknown image.")
        db.set_cover(path, album_id, uri)
        return RedirectResponse(f"/albums/{album_id}#art", status_code=303)

    @router.post("/albums/{album_id}/review")
    async def save_review(request: Request, album_id: int):
        form = await checked_form(request)
        album_or_404(album_id)
        rating, error = parse_rating(form.get("rating"))
        review = str(form.get("review", "")).strip()
        if error or len(review) > MAX_REVIEW:
            raise HTTPException(status_code=400, detail=error or "Review is too long.")
        db.set_review(path, album_id, rating, review)
        return RedirectResponse(f"/albums/{album_id}#review", status_code=303)

    @router.get("/albums/{album_id}/tracks")
    async def track_form(request: Request, album_id: int):
        album = album_or_404(album_id)
        ratings = db.get_track_ratings(path, album_id)
        tracks = [
            {
                **t,
                "rating": ratings[t["position"]]["rating"] if t["position"] in ratings else None,
                "note": ratings[t["position"]]["note"] if t["position"] in ratings else "",
            }
            for t in release_tracks(album_id)
        ]
        return render(request, "tracks.html", album=album, tracks=tracks, ratings=RATINGS)

    @router.post("/albums/{album_id}/tracks")
    async def save_tracks(request: Request, album_id: int):
        form = await checked_form(request)
        album_or_404(album_id)
        rows = []
        # Positions and titles come from the stored tracklist, never from the form.
        for i, track in enumerate(release_tracks(album_id)):
            rating, error = parse_rating(form.get(f"rating_{i}"))
            note = str(form.get(f"note_{i}", "")).strip()
            if error or len(note) > MAX_TRACK_NOTE:
                raise HTTPException(status_code=400, detail=error or "Track note is too long.")
            rows.append((track["position"] or str(i + 1), track["title"], rating, note))
        db.save_track_ratings(path, album_id, rows)
        return RedirectResponse(f"/albums/{album_id}#tracks", status_code=303)

    @router.post("/albums/{album_id}/delete")
    async def delete_album(request: Request, album_id: int):
        await checked_form(request)
        if not db.delete_album(path, album_id):
            raise HTTPException(status_code=404, detail="Album not found.")
        return RedirectResponse("/", status_code=303)

    @router.get("/import")
    async def import_page(request: Request):
        return render(
            request,
            "import.html",
            has_token=service.has_token,
            status=import_state,
            conditions=CONDITIONS,
            queued=lookups.pending(),
        )

    @router.post("/import")
    async def start_import(request: Request):
        form = await checked_form(request)
        condition = form.get("condition")
        if condition not in CONDITIONS:
            raise HTTPException(status_code=400, detail="Choose a condition.")
        if not service.has_token:
            raise HTTPException(status_code=400, detail="Set MYVINYL_DISCOGS_TOKEN first.")
        with import_lock:
            if import_state["state"] == "running":
                return RedirectResponse("/import", status_code=303)
            import_state.update(state="running", added=0, skipped=0, message="")
        if inline_jobs:
            run_import(condition)
        else:
            threading.Thread(target=run_import, args=(condition,), daemon=True).start()
        return RedirectResponse("/import", status_code=303)

    @router.get("/export.csv")
    async def export_csv(request: Request):
        buf = io.StringIO()
        writer = csv.writer(buf)
        fields = ["artist", "title", "year", "label", "format", "condition", "notes"]
        money = ["value_cents", "value_low_cents", "value_median_cents", "value_high_cents"]
        writer.writerow(
            [
                *fields,
                "value",
                "value_low",
                "value_median",
                "value_high",
                "value_source",
                "discogs_release_id",
                "pressing_picked",
                "barcode_or_catno",
                "rating",
                "review",
            ]
        )
        for a in db.list_albums(path):
            amounts = ["" if a[m] is None else f"{a[m] / 100:.2f}" for m in money]
            row = [
                *(a[f] for f in fields),
                *amounts,
                a["value_source"],
                a["discogs_release_id"],
                "yes" if a["pressing_locked"] else "",
                a["identifier"],
                a["rating"],
                a["review"],
            ]
            writer.writerow([csv_safe(v) for v in row])
        return Response(
            buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="myvinyl.csv"'},
        )

    app.include_router(router)
    return app


def app_from_env() -> FastAPI:
    """Factory used by uvicorn: builds the app from environment settings."""
    return create_app(Settings.from_env())
