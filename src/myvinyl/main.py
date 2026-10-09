"""myvinyl web application."""

import csv
import functools
import hmac
import io
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import db, discogs
from .albums import CONDITIONS, FORMATS, album_to_values, format_money, parse_album
from .auth import LoginLimiter, verify_password
from .config import Settings

PKG_DIR = Path(__file__).parent
SESSION_MAX_AGE = 12 * 60 * 60  # 12 hours
MAX_QUERY_LEN = 200
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

# (artist, title, year) -> Discogs enrichment, or None when nothing matches.
# Injectable so tests never touch the network.
EnrichFn = Callable[[str, str, int | None], discogs.Enrichment | None]


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


def create_app(settings: Settings, enricher: EnrichFn | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=PKG_DIR / "templates")
    templates.env.filters["money"] = format_money
    templates.env.filters["eastern"] = eastern_time
    limiter = LoginLimiter()
    db.init(settings.db_path)
    if enricher is None:
        client = discogs.DiscogsClient(settings.discogs_token)
        enricher = functools.partial(discogs.enrich, client)

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

    def run_enrichment(album_id: int, artist: str, title: str, year: int | None) -> None:
        """Background task: Discogs lookup for one album. Never raises."""
        try:
            result = enricher(artist, title, year)
            if result is None:
                db.set_discogs_status(settings.db_path, album_id, "none")
            else:
                db.save_enrichment(settings.db_path, album_id, result)
        except discogs.LOOKUP_ERRORS as exc:
            log.warning("Discogs lookup failed for album %s: %s", album_id, exc)
            db.set_discogs_status(settings.db_path, album_id, "error")
        except Exception:
            log.exception("unexpected error during Discogs lookup for album %s", album_id)
            db.set_discogs_status(settings.db_path, album_id, "error")

    def queue_lookup(tasks: BackgroundTasks, album) -> None:
        db.set_discogs_status(settings.db_path, album["id"], "pending")
        tasks.add_task(run_enrichment, album["id"], album["artist"], album["title"], album["year"])

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
        album = db.get_album(settings.db_path, album_id)
        if album is None:
            raise HTTPException(status_code=404, detail="Album not found.")
        return album

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
            albums=db.list_albums(settings.db_path, q, sort, direction),
            q=q,
            sort=sort,
            direction=direction,
            totals=db.totals(settings.db_path),
        )

    @router.get("/albums/new")
    async def new_album(request: Request):
        return album_form(request)

    @router.post("/albums")
    async def create_album(request: Request, tasks: BackgroundTasks):
        data, values, errors = parse_album(await checked_form(request))
        if errors:
            return album_form(request, values=values, errors=errors, status_code=422)
        album_id = db.create_album(settings.db_path, data)
        # Range and metadata are always looked up; the value only if you left it blank.
        queue_lookup(tasks, db.get_album(settings.db_path, album_id))
        return RedirectResponse(f"/albums/{album_id}", status_code=303)

    @router.get("/albums/{album_id}")
    async def album_detail(request: Request, album_id: int):
        album = album_or_404(album_id)
        release, pressings = db.get_discogs(settings.db_path, album_id)
        summary = discogs.release_summary(release) if release else None
        cover = ""
        if summary:
            # Your pick if it is still among the release's images, else the default.
            uris = {i["uri"] for i in summary["images"]}
            cover = album["cover_uri"] if album["cover_uri"] in uris else summary["cover"]
        return render(
            request,
            "album.html",
            album=album,
            release=summary,
            cover=cover,
            pressings=pressings,
        )

    @router.post("/albums/{album_id}/cover")
    async def choose_cover(request: Request, album_id: int):
        form = await checked_form(request)
        album_or_404(album_id)
        release, _ = db.get_discogs(settings.db_path, album_id)
        uri = form.get("uri")
        # Only accept one of the release's own image URLs, never an arbitrary URL.
        allowed = {i["uri"] for i in discogs.release_images(release or {})}
        if not isinstance(uri, str) or uri not in allowed:
            raise HTTPException(status_code=400, detail="Unknown image.")
        db.set_cover(settings.db_path, album_id, uri)
        return RedirectResponse(f"/albums/{album_id}#art", status_code=303)

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
        db.update_album(settings.db_path, album_id, data, source)
        return RedirectResponse(f"/albums/{album_id}", status_code=303)

    @router.post("/albums/{album_id}/lookup")
    async def lookup_album(request: Request, album_id: int, tasks: BackgroundTasks):
        await checked_form(request)
        album = album_or_404(album_id)
        if album["discogs_status"] != "pending":
            queue_lookup(tasks, album)
        return RedirectResponse(f"/albums/{album_id}", status_code=303)

    @router.post("/albums/{album_id}/delete")
    async def delete_album(request: Request, album_id: int):
        await checked_form(request)
        if not db.delete_album(settings.db_path, album_id):
            raise HTTPException(status_code=404, detail="Album not found.")
        return RedirectResponse("/", status_code=303)

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
            ]
        )
        for a in db.list_albums(settings.db_path):
            amounts = ["" if a[m] is None else f"{a[m] / 100:.2f}" for m in money]
            row = [*(a[f] for f in fields), *amounts, a["value_source"], a["discogs_release_id"]]
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
