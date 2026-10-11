"""myvinyl web application."""

import asyncio
import csv
import hmac
import io
import json
import logging
import secrets
import sqlite3
import threading
import urllib.error
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.middleware.sessions import SessionMiddleware

from . import __version__, charts, db, discogs, guide, sync
from .albums import (
    CONDITIONS,
    FORMATS,
    MAX_NOTE,
    MAX_REVIEW,
    MAX_TRACK_NOTE,
    RATINGS,
    ROLES,
    SLEEVE_CONDITIONS,
    THEMES,
    album_to_values,
    check_new_password,
    format_money,
    format_stars,
    parse_album,
    parse_email,
    parse_identifier,
    parse_money,
    parse_profile,
    parse_rating,
    parse_username,
    parse_wish,
)
from .auth import LoginLimiter, hash_password, hash_token, new_link_token, verify_password
from .config import ConfigError, Settings
from .lookups import BULK, INTERACTIVE, JobQueue, PeriodicTask
from .storage import COVER_EXTENSIONS, Storage
from .tokens import TokenBox, hint, valid_token

PKG_DIR = Path(__file__).parent
SESSION_MAX_AGE = 12 * 60 * 60  # 12 hours
MAX_QUERY_LEN = 200
MAX_ALBUMS_PER_USER = 20_000  # caps Discogs work any one account can cause
# Cover uploads: the image cap plus room for the multipart envelope and CSRF field.
MAX_UPLOAD_BYTES = discogs.MAX_IMAGE_BYTES + 64_000
HASH_SLOTS = 2  # concurrent Argon2 operations (each uses ~64 MB and ~150 ms of CPU)
REFRESH_BATCH = 20  # albums (and wishes) re-queued per hourly run
LINK_DAYS = 7  # invite and reset links expire after a week
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
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class NotAuthenticated(Exception):
    pass


# --- Request helpers ---------------------------------------------------------------------


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


async def checked_form(request: Request, **limits):
    """Read a POSTed form, rejecting it unless the CSRF token matches the session.

    `limits` are passed to Starlette's parser (max_files, max_fields, max_part_size).
    """
    form = await request.form(**limits)
    sent = form.get("csrf")
    expected = request.session.get("csrf")
    if not expected or not isinstance(sent, str) or not hmac.compare_digest(sent, expected):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
    return form


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


def parse_filters(params) -> tuple[db.Filters, dict[str, str]]:
    """Filters from the query string. Invalid values are ignored (they only narrow a view)."""
    raw = {k: str(params.get(k, "")).strip()[:MAX_QUERY_LEN] for k in db.Filters.__annotations__}
    decade = positive_int(raw["decade"])
    rating, _ = parse_rating(raw["min_rating"])
    low, _ = parse_money(raw["min_value"])
    high, _ = parse_money(raw["max_value"])
    f = db.Filters(
        q=raw["q"],
        genre=raw["genre"][:60],
        decade=decade if decade and decade % 10 == 0 and 1900 <= decade <= 2100 else None,
        format=raw["format"] if raw["format"] in FORMATS else "",
        condition=raw["condition"] if raw["condition"] in CONDITIONS else "",
        min_rating=rating,
        min_value=low,
        max_value=high,
        folder=positive_int(raw["folder"]),
    )
    clean = {k: v for k, v in raw.items() if v and getattr(f, k) not in ("", None)}
    return f, clean


# --- App factory -------------------------------------------------------------------------


def create_app(
    settings: Settings,
    service: discogs.DiscogsService | None = None,
    inline_jobs: bool = False,
    service_factory=None,
) -> FastAPI:
    """Build the app. Tests pass a fake `service` and inline_jobs=True (no threads)."""
    if len(settings.token_key) != 32:
        raise ConfigError("MYVINYL_TOKEN_KEY must be set (32 bytes, base64).")
    token_box = TokenBox(settings.token_key)

    # Discogs access: each user's own token, or anonymous access without one. Tests pass
    # a fake `service` (used for everyone) or a `service_factory(token)`.
    if service_factory is None:
        if service is not None:
            service_factory = lambda token: service  # noqa: E731
        else:

            def service_factory(token: str) -> discogs.DiscogsService:
                return discogs.DiscogsService(discogs.DiscogsClient(token), bool(token))

    anonymous = service_factory("")
    services: dict[int, tuple[str, object]] = {}  # user id -> (token, service)
    services_lock = threading.Lock()

    path = settings.db_path
    db.init(path)
    storage = Storage(path)

    def user_token(user) -> str:
        """The user's decrypted Discogs token, or "" (none, or it can't be decrypted)."""
        return token_box.open(user["id"], user["discogs_token"]) if user else ""

    def discogs_for(owner: int | None):
        """Discogs access for work on this user's collection: their token, else anonymous.

        Disabled accounts' tokens are never used. Work an admin starts while viewing
        someone else's collection runs as the admin (see `acting_discogs`).
        """
        user = db.get_user(path, owner) if owner else None
        token = user_token(user) if user and not user["disabled"] else ""
        if not token:
            return anonymous
        with services_lock:
            cached = services.get(owner)
            if cached is None or not secrets.compare_digest(cached[0], token):
                cached = (token, service_factory(token))
                services[owner] = cached
            return cached[1]

    def forget_service(user_id: int) -> None:
        with services_lock:
            services.pop(user_id, None)

    def acting_discogs(request: Request):
        """Discogs access for work started by a request: always the signed-in user's own
        token, so an admin viewing another collection never acts with that user's token."""
        return discogs_for(request.state.user["id"])

    # First run: create the admin account from MYVINYL_PASSWORD_HASH.
    if db.count_users(path) == 0:
        if not settings.password_hash:
            raise ConfigError("No accounts yet: set MYVINYL_PASSWORD_HASH to create the admin.")
        admin_id = db.create_user(path, settings.admin_username, settings.password_hash, "admin")
        log.info("created admin account %r", settings.admin_username)
        db.adopt_orphan_albums(path, admin_id)

    # --- Background jobs -----------------------------------------------------------

    # Serializes cover writes so a background Discogs download can't overwrite a cover
    # you uploaded while it was in flight.
    cover_lock = threading.Lock()

    def save_cover(album_id: int) -> None:
        """Download the displayed cover and keep a local copy."""
        album = db.get_album(path, album_id, include_deleted=True)
        release, _ = db.get_discogs(path, album_id)
        if album is None or not release or album["cover_custom"]:
            return
        summary = discogs.release_summary(release)
        uris = {i["uri"] for i in summary["images"]}
        uri = album["cover_uri"] if album["cover_uri"] in uris else summary["cover"]
        if not uri:
            return
        try:
            data, ext = discogs_for(album["owner_id"]).fetch_image(uri)
        except (*discogs.LOOKUP_ERRORS, ValueError) as exc:
            log.warning("cover download failed for album %s: %s", album_id, exc)
            return
        with cover_lock:
            album = db.get_album(path, album_id, include_deleted=True)
            if album is None or album["cover_custom"]:
                return  # you uploaded your own cover meanwhile
            db.set_cover_file(path, album_id, storage.save_cover(album_id, data, ext))

    def store_custom_cover(album_id: int, data: bytes, ext: str) -> None:
        with cover_lock:
            db.set_cover_file(path, album_id, storage.save_cover(album_id, data, ext), True)

    def drop_custom_cover(album_id: int) -> None:
        with cover_lock:
            album = db.get_album(path, album_id)
            if album and album["cover_custom"]:
                storage.delete_cover(album["cover_file"])
                db.set_cover_file(path, album_id, None)

    def run_album(album_id: int) -> None:
        album = db.get_album(path, album_id)
        if album is None:
            return  # deleted while queued
        pinned = album["discogs_release_id"] if album["pressing_locked"] else None
        try:
            svc = discogs_for(album["owner_id"])
            result = svc.enrich(album["artist"], album["title"], album["year"], pinned)
        except discogs.LOOKUP_ERRORS as exc:
            log.warning("Discogs lookup failed for album %s: %s", album_id, exc)
            db.set_discogs_status(path, album_id, "error")
            return
        if result is None:
            db.set_discogs_status(path, album_id, "none")
            return
        db.save_enrichment(path, album_id, result)
        save_cover(album_id)

    def run_album_light(album_id: int) -> None:
        """Price and details for a synced album whose pressing is known (2 API calls).
        The hourly refresh does the full lookup with the price range later."""
        album = db.get_album(path, album_id)
        if album is None or not album["discogs_release_id"]:
            return
        try:
            result = discogs_for(album["owner_id"]).enrich_pinned(album["discogs_release_id"])
        except discogs.LOOKUP_ERRORS as exc:
            log.warning("Discogs light lookup failed for album %s: %s", album_id, exc)
            db.set_discogs_status(path, album_id, "error")
            return
        db.save_enrichment(path, album_id, result, full=False)
        save_cover(album_id)

    def run_wish(wish_id: int) -> None:
        wish = db.get_wish(path, wish_id)
        if wish is None:
            return
        pinned = wish["discogs_release_id"] if wish["pressing_locked"] else None
        try:
            svc = discogs_for(wish["owner_id"])
            found = svc.price_wish(wish["artist"], wish["title"], wish["year"], pinned)
        except discogs.LOOKUP_ERRORS as exc:
            log.warning("Discogs price check failed for wish %s: %s", wish_id, exc)
            db.set_wish_status(path, wish_id, "error")
            return
        if found is None:
            db.set_wish_status(path, wish_id, "none")
        else:
            db.save_wish_price(path, wish_id, *found)

    def run_job(job) -> None:
        kind, item_id = job
        try:
            runners = {
                "album": run_album,
                "album_light": run_album_light,
                "wish": run_wish,
                "cover": save_cover,
            }
            runners[kind](item_id)
        except Exception:
            log.exception("job %r failed", job)
            if kind in ("album", "album_light"):
                db.set_discogs_status(path, item_id, "error")
            elif kind == "wish":
                db.set_wish_status(path, item_id, "error")

    jobs = JobQueue(run_job, inline=inline_jobs)

    def queue_album(album_id: int, priority: int = INTERACTIVE) -> None:
        db.set_discogs_status(path, album_id, "pending")
        jobs.submit(("album", album_id), priority)

    def queue_light(album_id: int) -> None:
        db.set_discogs_status(path, album_id, "pending")
        jobs.submit(("album_light", album_id), BULK)

    def queue_wish(wish_id: int, priority: int = INTERACTIVE) -> None:
        db.set_wish_status(path, wish_id, "pending")
        jobs.submit(("wish", wish_id), priority)

    def purge_album(album_id: int) -> None:
        cover = db.purge(path, album_id)
        if cover:
            storage.delete_cover(cover)

    def hourly() -> str:
        """Re-check stale prices and empty old trash."""
        done = []
        if settings.refresh_days > 0:
            albums = db.due_for_refresh(path, settings.refresh_days, REFRESH_BATCH)
            for album_id in albums:
                queue_album(album_id, BULK)
            wishes = db.wishes_due(path, settings.refresh_days, REFRESH_BATCH)
            for wish_id in wishes:
                queue_wish(wish_id, BULK)
            if albums or wishes:
                done.append(f"queued {len(albums)} albums and {len(wishes)} wishes for refresh")
        pruned = db.prune_sync_log(path)
        if pruned:
            done.append(f"pruned {pruned} old sync log rows")
        if settings.trash_days > 0:
            old = db.trash_due(path, settings.trash_days)
            for album_id in old:
                purge_album(album_id)
            if old:
                done.append(f"purged {len(old)} albums from the trash")
        return "; ".join(done)

    def backup() -> str:
        return f"backup {storage.backup(settings.backup_keep).name}"

    tasks = [PeriodicTask("hourly-maintenance", hourly, 3600)]
    if settings.backup_hours > 0:
        tasks.append(PeriodicTask("backup", backup, settings.backup_hours * 3600, 300))

    # Import jobs, one per collection owner at a time.
    imports: dict[tuple[str, int], dict] = {}
    imports_lock = threading.Lock()

    def import_status(kind: str, owner_id: int) -> dict:
        return imports.get((kind, owner_id), {"state": "idle", "added": 0, "skipped": 0})

    def run_collection_import(owner_id: int, svc, username: str, condition: str) -> None:
        state = imports[("collection", owner_id)]
        try:
            for item in svc.collection(username):
                if db.count_albums(path, owner_id) >= MAX_ALBUMS_PER_USER:
                    state.update(state="error", message="The collection is at its size limit.")
                    return
                if db.album_for_release(path, owner_id, item["release_id"]):
                    state["skipped"] += 1
                    continue
                year = item["year"] if item["year"] and 1900 <= item["year"] <= 2100 else None
                data = {
                    "artist": item["artist"],
                    "title": item["title"],
                    "year": year,
                    "label": item["label"],
                    "format": item["format"],
                    "condition": condition,
                    "notes": "",
                    "value_cents": None,
                }
                album_id = db.create_album(
                    path, owner_id, data, release_id=item["release_id"], rating=item["rating"]
                )
                queue_album(album_id, BULK)
                state["added"] += 1
            state["state"] = "done"
        except (*discogs.LOOKUP_ERRORS, ValueError) as exc:
            log.warning("Discogs collection import failed: %s", exc)
            state.update(state="error", message=import_error(exc))
        except Exception:
            log.exception("Discogs collection import failed")
            state.update(state="error", message="The import stopped unexpectedly.")

    def run_wantlist_import(owner_id: int, svc, username: str) -> None:
        state = imports[("wantlist", owner_id)]
        try:
            for item in svc.wantlist(username):
                if db.wish_for_release(path, owner_id, item["release_id"]):
                    state["skipped"] += 1
                    continue
                year = item["year"] if item["year"] and 1900 <= item["year"] <= 2100 else None
                wish_id = db.create_wish(
                    path,
                    owner_id,
                    {**item, "year": year, "target_cents": None},
                    release_id=item["release_id"],
                )
                queue_wish(wish_id, BULK)
                state["added"] += 1
            state["state"] = "done"
        except (*discogs.LOOKUP_ERRORS, ValueError) as exc:
            log.warning("Discogs wantlist import failed: %s", exc)
            state.update(state="error", message=import_error(exc))
        except Exception:
            log.exception("Discogs wantlist import failed")
            state.update(state="error", message="The import stopped unexpectedly.")

    def import_error(exc: Exception) -> str:
        code = getattr(exc, "code", None)
        if code in (401, 403):
            return "Discogs refused access. Is that collection public?"
        if code == 404:
            return "Discogs has no user by that name."
        return "Discogs could not be reached."

    def start_import(kind: str, owner_id: int, target, *args) -> bool:
        with imports_lock:
            if import_status(kind, owner_id)["state"] == "running":
                return False
            imports[(kind, owner_id)] = {"state": "running", "added": 0, "skipped": 0}
        if inline_jobs:
            target(owner_id, *args)
        else:
            threading.Thread(target=target, args=(owner_id, *args), daemon=True).start()
        return True

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        jobs.start()
        if not inline_jobs:
            for task in tasks:
                task.start()
        yield
        for task in tasks:
            task.stop()
        jobs.stop()

    # --- App setup -------------------------------------------------------------------

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.hourly = hourly  # exposed for tests and manual triggering
    app.state.storage = storage
    templates = Jinja2Templates(directory=PKG_DIR / "templates")
    templates.env.filters["money"] = format_money
    templates.env.filters["eastern"] = eastern_time
    templates.env.filters["eastern_date"] = eastern_date
    templates.env.filters["stars"] = format_stars
    templates.env.filters["fromjson"] = json.loads
    templates.env.globals["version"] = __version__
    # The guide is package content rendered with raw HTML escaped, so it is safe to embed.
    help_pages = {
        name: (Markup(part.html), part.contents)  # noqa: S704 (package Markdown, HTML escaped)
        for name, part in guide.load().items()
    }
    limiter = LoginLimiter()
    # Wrong current passwords on Profile forms, per account. Five within 15 minutes
    # locks those forms and signs the account out everywhere (a stolen session can't
    # keep guessing the password, or tie up the Argon2 slots that logins need).
    reauth_limiter = LoginLimiter()
    hash_slots = asyncio.Semaphore(HASH_SLOTS)

    async def check_password(password_hash: str | None, password: str) -> bool:
        # Argon2 is deliberately slow; run it on a worker thread so one login can't stall
        # every other request, and cap concurrency so a login flood can't exhaust memory.
        async with hash_slots:
            return await run_in_threadpool(verify_password, password_hash, password)

    async def recheck_password(request: Request, password) -> bool:
        """Check the signed-in user's current password before a sensitive change."""
        user = request.state.user
        key = f"user:{user['id']}"
        if reauth_limiter.blocked(key):
            raise HTTPException(status_code=429, detail="Too many wrong passwords. Try later.")
        if isinstance(password, str) and await check_password(user["password_hash"], password):
            reauth_limiter.reset(key)
            return True
        reauth_limiter.record_failure(key)
        log.warning("wrong current password on a profile form for user %s", user["id"])
        if reauth_limiter.blocked(key):
            log.warning("user %s signed out everywhere after repeated wrong passwords", user["id"])
            db.bump_session_version(path, user["id"])
        return False

    async def make_hash(password: str) -> str:
        async with hash_slots:
            return await run_in_threadpool(hash_password, password)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="myvinyl_session",
        max_age=SESSION_MAX_AGE,
        same_site="lax",
        https_only=True,  # Secure flag; browsers still accept it on http://localhost
    )
    app.mount("/static", StaticFiles(directory=PKG_DIR / "static"), name="static")

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
        context.setdefault("me", getattr(request.state, "user", None))
        context.setdefault("viewing", getattr(request.state, "viewing", None))
        return templates.TemplateResponse(request, name, context, status_code=status_code)

    # --- Access control ------------------------------------------------------------

    def load_user(request: Request):
        """Every signed-in route: a live, enabled account whose session is still current."""
        user_id = request.session.get("uid")
        user = db.get_user(path, user_id) if isinstance(user_id, int) else None
        if user is None or user["disabled"] or request.session.get("sv") != user["session_version"]:
            request.session.clear()
            raise NotAuthenticated()
        request.state.user = user
        request.state.viewing = None
        view = request.session.get("view_owner")
        if user["role"] == "admin" and isinstance(view, int) and view != user["id"]:
            request.state.viewing = db.get_user(path, view)
        return user

    def require_admin(request: Request):
        user = load_user(request)
        if user["role"] != "admin":
            raise HTTPException(status_code=404)
        return user

    def owner_id(request: Request) -> int:
        """Whose collection this request works on: yours, or the one an admin is viewing."""
        viewing = request.state.viewing
        return viewing["id"] if viewing else request.state.user["id"]

    def album_access(request: Request, album_id: int, include_deleted: bool = False):
        """The album if you own it (or are an admin); 404 otherwise, so ids can't be probed."""
        album = db.get_album(path, album_id, include_deleted=include_deleted)
        user = request.state.user
        if album is None or (album["owner_id"] != user["id"] and user["role"] != "admin"):
            raise HTTPException(status_code=404, detail="Album not found.")
        return album

    def wish_access(request: Request, wish_id: int):
        wish = db.get_wish(path, wish_id)
        user = request.state.user
        if wish is None or (wish["owner_id"] != user["id"] and user["role"] != "admin"):
            raise HTTPException(status_code=404, detail="Not found.")
        return wish

    def sign_in(request: Request, user) -> None:
        request.session.clear()  # fresh session (and CSRF token) on every sign-in
        request.session["uid"] = user["id"]
        request.session["sv"] = user["session_version"]
        db.touch_login(path, user["id"])

    def link_url(request: Request, token: str) -> str:
        base = settings.base_url or str(request.base_url).rstrip("/")
        return f"{base}/link/{token}"

    # --- Forms -----------------------------------------------------------------------

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
            sleeve_conditions=SLEEVE_CONDITIONS,
        )

    def release_tracks(album_id: int) -> list[dict]:
        release, _ = db.get_discogs(path, album_id)
        return discogs.release_summary(release)["tracks"] if release else []

    # --- Public routes ---------------------------------------------------------------

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return FileResponse(PKG_DIR / "static" / "favicon.ico")

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return PlainTextResponse("ok")

    @app.get("/login")
    async def login_page(request: Request):
        if request.session.get("uid"):
            return RedirectResponse("/", status_code=303)
        return render(request, "login.html")

    @app.post("/login")
    async def login(request: Request):
        form = await checked_form(request)
        ip = request.client.host if request.client else "unknown"
        username = str(form.get("username", "")).strip()[:64]
        keys = (f"ip:{ip}", f"user:{username.casefold()}")
        if limiter.blocked(*keys):
            log.warning("login blocked by rate limit: ip=%s", ip)
            return render(
                request,
                "login.html",
                429,
                error="Too many attempts. Try again later.",
                username=username,
            )
        password = form.get("password")
        user = db.get_user_by_name(path, username) if username else None
        ok = (
            isinstance(password, str)
            and len(password) <= 1024
            and await check_password(user["password_hash"] if user else None, password)
        )
        if ok and not user["disabled"]:
            limiter.reset(*keys)
            sign_in(request, user)
            return RedirectResponse("/", status_code=303)
        limiter.record_failure(*keys)
        log.warning("failed login: ip=%s", ip)
        # Same message for unknown user, wrong password, or disabled account.
        return render(
            request, "login.html", 401, error="Incorrect username or password.", username=username
        )

    @app.get("/link/{token}")
    async def link_page(request: Request, token: str):
        link = db.get_link(path, hash_token(token[:100]))
        if link is None:
            return render(request, "link.html", 404, invalid=True)
        target = db.get_user(path, link["user_id"]) if link["user_id"] else None
        return render(request, "link.html", link=link, target=target, token=token, errors={})

    @app.post("/link/{token}")
    async def use_link(request: Request, token: str):
        form = await checked_form(request)
        ip = request.client.host if request.client else "unknown"
        if limiter.blocked(f"ip:{ip}"):
            raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")
        link = db.get_link(path, hash_token(token[:100]))
        if link is None:
            limiter.record_failure(f"ip:{ip}")
            return render(request, "link.html", 404, invalid=True)
        target = db.get_user(path, link["user_id"]) if link["user_id"] else None
        errors = {}
        password_error = check_new_password(form.get("password"), form.get("confirm"))
        if password_error:
            errors["password"] = password_error
        username, email = "", None
        if link["kind"] == "invite":
            username, error = parse_username(form.get("username"))
            if error:
                errors["username"] = error
            elif db.get_user_by_name(path, username):
                errors["username"] = "That username is taken."
            email, error = parse_email(form.get("email"))
            if error:
                errors["email"] = error
            elif email and db.email_in_use(path, email):
                errors["email"] = "That email is already used by another account."
        if errors:
            return render(
                request,
                "link.html",
                422,
                link=link,
                target=target,
                token=token,
                errors=errors,
                username=str(form.get("username", ""))[:32],
                email=str(form.get("email", ""))[:254],
            )
        if not db.use_link(path, link["id"]):
            return render(request, "link.html", 404, invalid=True)
        new_hash = await make_hash(form["password"])
        if link["kind"] == "invite":
            try:
                user_id = db.create_user(path, username, new_hash, link["role"], email)
            except sqlite3.IntegrityError:  # someone took the name a moment ago
                return render(
                    request,
                    "link.html",
                    422,
                    link=link,
                    target=target,
                    token=token,
                    errors={"username": "That username is taken."},
                    username=username,
                )
        else:
            if target is None:
                return render(request, "link.html", 404, invalid=True)
            user_id = target["id"]
            db.set_password(path, user_id, new_hash)
        sign_in(request, db.get_user(path, user_id))
        return RedirectResponse("/", status_code=303)

    # --- Signed-in routes ------------------------------------------------------------

    router = APIRouter(dependencies=[Depends(load_user)])

    @router.post("/logout")
    async def logout(request: Request):
        await checked_form(request)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    def account_page_response(request, status_code=200, **extra):
        context = {
            "errors": {},
            "saved": "",
            "themes": THEMES,
            "profile": None,
            # Only the last 4 characters of your token are ever shown.
            "token_hint": hint(user_token(request.state.user)),
            **extra,
        }
        return render(request, "account.html", status_code, **context)

    @router.post("/account/discogs-token")
    async def save_discogs_token(request: Request):
        """Add or replace your Discogs token. Requires your current password, and the
        token must work on Discogs before it is stored (encrypted)."""
        form = await checked_form(request)
        user = request.state.user
        token = str(form.get("token", "")).strip()
        current = form.get("current")
        errors = {}
        if not valid_token(token):
            errors["discogs_key"] = (
                "That doesn't look like a Discogs token (20 to 100 letters and digits)."
            )
        if not await recheck_password(request, current):
            errors["key_current"] = "That isn't your current password."
        if not errors:
            try:
                username = await run_in_threadpool(service_factory(token).identity)
            except urllib.error.HTTPError as exc:
                log.info("Discogs token check for user %s: HTTP %s", user["id"], exc.code)
                errors["discogs_key"] = (
                    "Discogs didn't accept this token. Check it and try again."
                    if exc.code in (401, 403)
                    else "Discogs couldn't check the token right now. Try again."
                )
            except (*discogs.LOOKUP_ERRORS, ValueError):
                errors["discogs_key"] = "Discogs couldn't be reached to check the token. Try again."
        if errors:
            return account_page_response(request, 422, errors=errors)
        db.set_discogs_token(path, user["id"], token_box.seal(user["id"], token), username)
        db.set_discogs_username_if_blank(path, user["id"], username)
        forget_service(user["id"])
        log.info("user %s saved a Discogs token", user["id"])
        request.state.user = db.get_user(path, user["id"])
        return account_page_response(request, saved="token")

    @router.post("/account/discogs-token/delete")
    async def delete_discogs_token(request: Request):
        form = await checked_form(request)
        user = request.state.user
        current = form.get("current")
        if not await recheck_password(request, current):
            errors = {"key_remove_current": "That isn't your current password."}
            return account_page_response(request, 422, errors=errors)
        db.set_discogs_token(path, user["id"], None)
        forget_service(user["id"])
        log.info("user %s removed their Discogs token", user["id"])
        request.state.user = db.get_user(path, user["id"])
        return account_page_response(request, saved="token_removed")

    @router.get("/account")
    async def account_page(request: Request):
        return account_page_response(request)

    @router.post("/account/identity")
    async def change_own_identity(request: Request):
        """Change your username and/or email. Requires your current password, because
        a future email-based password reset would make the address security-sensitive."""
        form = await checked_form(request)
        user = request.state.user
        username, username_error = parse_username(form.get("username"))
        email, email_error = parse_email(form.get("email"))
        current = form.get("current")
        errors = {}
        if username_error:
            errors["username"] = username_error
        if email_error:
            errors["email"] = email_error
        if not await recheck_password(request, current):
            errors["identity_current"] = "That isn't your current password."
        if not errors:
            clash = db.set_identity(path, user["id"], username, email)
            if clash:
                errors[clash] = f"That {clash} is already used by another account."
        if errors:
            return account_page_response(request, 422, errors=errors)
        request.state.user = db.get_user(path, user["id"])
        log.info("user %s updated their username/email", user["id"])
        return account_page_response(request, saved="identity")

    @router.post("/account/sign-out-everywhere")
    async def sign_out_everywhere(request: Request):
        """Invalidate every session for this account (e.g. after using a shared computer)."""
        await checked_form(request)
        db.bump_session_version(path, request.state.user["id"])
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @router.post("/account/profile")
    async def save_profile(request: Request):
        form = await checked_form(request)
        profile, errors = parse_profile(form)
        if errors:
            return account_page_response(request, 422, errors=errors, profile=profile)
        db.set_profile(path, request.state.user["id"], profile)
        request.state.user = db.get_user(path, request.state.user["id"])
        return account_page_response(request, saved="profile")

    @router.post("/account")
    async def change_password(request: Request):
        form = await checked_form(request)
        user = request.state.user
        errors = {}
        current = form.get("current")
        if not await recheck_password(request, current):
            errors["current"] = "That isn't your current password."
        error = check_new_password(form.get("password"), form.get("confirm"))
        if error:
            errors["password"] = error
        if errors:
            return account_page_response(request, 422, errors=errors)
        db.set_password(path, user["id"], await make_hash(form["password"]))
        sign_in(request, db.get_user(path, user["id"]))  # stay signed in here only
        request.state.user = db.get_user(path, user["id"])
        return account_page_response(request, saved="password")

    # --- Collection views ------------------------------------------------------------

    def collection_context(request: Request) -> dict:
        params = request.query_params
        filters, clean = parse_filters(params)
        sort = params.get("sort", "artist")
        sort = sort if sort in db.SORTS else "artist"
        direction = "desc" if params.get("dir") == "desc" else "asc"
        owner = owner_id(request)
        return {
            "filters": filters,
            "f": clean,
            "fq": urlencode(clean),
            "sort": sort,
            "direction": direction,
            "albums": db.list_albums(path, owner, filters, sort, direction),
            "totals": db.totals(path, owner, filters),
            "options": db.filter_options(path, owner),
            "formats": FORMATS,
            "conditions": CONDITIONS,
            "ratings": RATINGS,
        }

    @router.get("/")
    async def index(request: Request):
        view = "grid" if request.query_params.get("view") == "grid" else "list"
        return render(
            request,
            "index.html",
            view=view,
            queued=jobs.pending("album") + jobs.pending("album_light"),
            review_count=0
            if request.state.viewing
            else db.count_sync_items(path, request.state.user["id"]),
            **collection_context(request),
        )

    @router.get("/report")
    async def report(request: Request):
        context = collection_context(request)
        return render(
            request,
            "report.html",
            generated=eastern_time(datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")),
            **context,
        )

    @router.get("/stats")
    async def stats_page(request: Request):
        context = collection_context(request)
        return render(
            request,
            "stats.html",
            stats=db.stats(path, owner_id(request), context["filters"]),
            **context,
        )

    @router.get("/export.csv")
    async def export_csv(request: Request):
        filters, _ = parse_filters(request.query_params)
        buf = io.StringIO()
        writer = csv.writer(buf)
        fields = [
            "artist",
            "title",
            "year",
            "label",
            "format",
            "condition",
            "sleeve_condition",
            "notes",
        ]
        money = [
            "value_cents",
            "value_low_cents",
            "value_median_cents",
            "value_high_cents",
            "purchase_cents",
        ]
        writer.writerow(
            [
                *fields,
                "value",
                "value_low",
                "value_median",
                "value_high",
                "purchase_price",
                "purchase_date",
                "purchased_from",
                "value_source",
                "discogs_release_id",
                "pressing_picked",
                "barcode_or_catno",
                "genres",
                "styles",
                "rating",
                "review",
                "discogs_folder",
                "discogs_added",
            ]
        )
        folders = db.folder_names(path, owner_id(request))
        for a in db.list_albums(path, owner_id(request), filters):
            amounts = ["" if a[m] is None else f"{a[m] / 100:.2f}" for m in money]
            row = [
                *(a[f] for f in fields),
                *amounts,
                a["purchase_date"],
                a["purchased_from"],
                a["value_source"],
                a["discogs_release_id"],
                "yes" if a["pressing_locked"] else "",
                a["identifier"],
                a["genres"].replace("|", ", "),
                a["styles"].replace("|", ", "),
                a["rating"],
                a["review"],
                folders.get(a["discogs_folder_id"], ""),
                a["discogs_added_at"] or "",
            ]
            writer.writerow([csv_safe(v) for v in row])
        return Response(
            buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="myvinyl.csv"'},
        )

    # --- Albums ------------------------------------------------------------------------

    async def match_identifier(request, identifier, raw, album=None):
        """Find the pressing for a barcode or catalog number.

        Returns the single Discogs match after filling the blanks in `raw` from it, or a
        response to send instead: the form with an error, or the page for choosing among
        several pressings. `album` is set when editing an existing album.
        """
        try:
            matches = await run_in_threadpool(
                acting_discogs(request).find_by_identifier, identifier
            )
        except discogs.LOOKUP_ERRORS as exc:
            log.warning("Discogs identifier search failed: %s", exc)
            matches = None
        album_id = album["id"] if album else None
        if not matches:
            _, values, _ = parse_album(raw)
            values["identifier"] = identifier
            message = (
                "Discogs could not be reached. Try again, or leave this blank."
                if matches is None
                else "No vinyl release on Discogs has that barcode or catalog number."
            )
            return album_form(
                request, album_id, values, {"identifier": message}, status_code=422, album=album
            )
        if len(matches) > 1:
            return render(
                request,
                "choose.html",
                identifier=identifier,
                matches=matches,
                form=raw,
                action=f"/albums/{album_id}" if album_id else "/albums",
                edit=album_id is not None,
            )
        match = matches[0]
        # Fill only what you left blank.
        for key in ("artist", "title", "label", "year"):
            if not str(raw.get(key, "")).strip() and match[key]:
                raw[key] = str(match[key])
        return match

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
            match = await match_identifier(request, identifier, raw)
            if isinstance(match, Response):
                return match
            release_id = match["release_id"]
            # Format comes from the release itself because the form's format menu always
            # has a value.
            raw["format"] = match["format"]

        data, values, errors = parse_album(raw)
        if errors:
            values["identifier"] = identifier
            return album_form(request, values=values, errors=errors, status_code=422)
        if db.count_albums(path, owner_id(request)) >= MAX_ALBUMS_PER_USER:
            raise HTTPException(status_code=400, detail="This collection is at its size limit.")
        album_id = db.create_album(
            path, owner_id(request), data, release_id=release_id, identifier=identifier
        )
        # Range and metadata are always looked up; the value only if you left it blank.
        queue_album(album_id)
        return RedirectResponse(f"/albums/{album_id}", status_code=303)

    @router.get("/albums/{album_id}")
    async def album_detail(request: Request, album_id: int):
        album = album_access(request, album_id)
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
        owner = db.get_user(path, album["owner_id"]) if album["owner_id"] else None
        return render(
            request,
            "album.html",
            album=album,
            owner=owner,
            release=summary,
            cover=cover,
            local_cover=bool(storage.cover_path(album["cover_file"])),
            tracks=tracks,
            pressings=pressings,
            chart=charts.value_history(db.get_history(path, album_id)),
            folder=db.folder_names(path, album["owner_id"]).get(album["discogs_folder_id"]),
            notes=db.list_notes(path, album_id),
            ratings=RATINGS,
        )

    @router.get("/covers/{album_id}")
    async def cover_image(request: Request, album_id: int):
        album = album_access(request, album_id, include_deleted=True)
        file = storage.cover_path(album["cover_file"])
        if file is None:
            raise HTTPException(status_code=404)
        ext = file.suffix.lstrip(".")
        return FileResponse(
            file,
            media_type=COVER_EXTENSIONS[ext],
            headers={"Cache-Control": "private, max-age=86400"},
        )

    @router.get("/albums/{album_id}/edit")
    async def edit_album(request: Request, album_id: int):
        album = album_access(request, album_id)
        values = {**album_to_values(album), "identifier": album["identifier"]}
        return album_form(request, album_id=album_id, values=values, album=album)

    @router.post("/albums/{album_id}")
    async def update_album(request: Request, album_id: int):
        form = await checked_form(request)
        album = album_access(request, album_id)
        identifier, id_error = parse_identifier(form.get("identifier"))
        # Set when you picked one of several pressings on the choose page.
        release_id = positive_int(form.get("release_id"))
        raw = dict(form)
        if id_error:
            _, values, errors = parse_album(raw)
            values["identifier"] = str(form.get("identifier", ""))[:60]
            errors["identifier"] = id_error
            return album_form(request, album_id, values, errors, status_code=422, album=album)

        if identifier and identifier != album["identifier"] and release_id is None:
            # A new barcode or catalog number: find the pressing again, as on Add.
            match = await match_identifier(request, identifier, raw, album=album)
            if isinstance(match, Response):
                return match
            release_id = match["release_id"]

        data, values, errors = parse_album(raw)
        if errors:
            values["identifier"] = identifier
            return album_form(request, album_id, values, errors, status_code=422, album=album)
        if data["value_cents"] is None:
            source = None  # cleared: the next Discogs lookup may fill it
        elif data["value_cents"] == album["value_cents"]:
            source = album["value_source"]
        else:
            source = "manual"  # you overrode the value; lookups won't replace it
        db.update_album(path, album_id, data, source)
        db.set_identifier(path, album_id, identifier)
        repinned = release_id is not None and (
            release_id != album["discogs_release_id"] or not album["pressing_locked"]
        )
        if repinned:
            # Pin the new pressing and refresh its value, details, and cover.
            db.set_pressing(path, album_id, release_id)
            queue_album(album_id)
        return RedirectResponse(f"/albums/{album_id}", status_code=303)

    @router.post("/albums/{album_id}/lookup")
    async def lookup_album(request: Request, album_id: int):
        await checked_form(request)
        album = album_access(request, album_id)
        if album["discogs_status"] != "pending":
            queue_album(album_id)
        return RedirectResponse(f"/albums/{album_id}", status_code=303)

    @router.post("/albums/{album_id}/pressing")
    async def choose_pressing(request: Request, album_id: int):
        """'This is my pressing': pin one of the checked pressings, or go back to auto."""
        form = await checked_form(request)
        album_access(request, album_id)
        if form.get("mode") == "auto":
            db.set_pressing(path, album_id, None)
        else:
            release_id = positive_int(form.get("release_id"))
            _, pressings = db.get_discogs(path, album_id)
            if release_id not in {p["release_id"] for p in pressings}:
                raise HTTPException(status_code=400, detail="Unknown pressing.")
            db.set_pressing(path, album_id, release_id)
        queue_album(album_id)
        return RedirectResponse(f"/albums/{album_id}", status_code=303)

    @router.post("/albums/{album_id}/cover")
    async def choose_cover(request: Request, album_id: int):
        form = await checked_form(request)
        album_access(request, album_id)
        release, _ = db.get_discogs(path, album_id)
        uri = form.get("uri")
        # Only accept one of the release's own image URLs, never an arbitrary URL.
        allowed = {i["uri"] for i in discogs.release_images(release or {})}
        if not isinstance(uri, str) or uri not in allowed:
            raise HTTPException(status_code=400, detail="Unknown image.")
        db.set_cover(path, album_id, uri)
        jobs.submit(("cover", album_id))
        return RedirectResponse(f"/albums/{album_id}#art", status_code=303)

    @router.post("/albums/{album_id}/cover/upload")
    async def upload_cover(request: Request, album_id: int):
        """Use your own image as the cover until you pick a Discogs image again."""
        # Refuse oversized bodies before parsing; Starlette spools file parts to disk
        # without a size cap of its own. Browsers always send Content-Length for forms.
        length = positive_int(request.headers.get("content-length"))
        if length is None or length > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Images can be up to 5 MB.")
        form = await checked_form(request, max_files=1, max_fields=5)
        album_access(request, album_id)
        upload = form.get("image")
        if not isinstance(upload, UploadFile):
            raise HTTPException(status_code=400, detail="Choose an image file.")
        data = await upload.read(discogs.MAX_IMAGE_BYTES + 1)
        if len(data) > discogs.MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Images can be up to 5 MB.")
        # Judged by content, never by the file name or the browser's content type.
        ext = discogs.image_extension(data)
        if ext is None:
            raise HTTPException(status_code=400, detail="Use a JPEG, PNG, WebP, or GIF image.")
        await run_in_threadpool(store_custom_cover, album_id, data, ext)
        return RedirectResponse(f"/albums/{album_id}#art", status_code=303)

    @router.post("/albums/{album_id}/cover/reset")
    async def reset_cover(request: Request, album_id: int):
        """Remove your uploaded cover and go back to the Discogs one."""
        await checked_form(request)
        album_access(request, album_id)
        await run_in_threadpool(drop_custom_cover, album_id)
        jobs.submit(("cover", album_id))
        return RedirectResponse(f"/albums/{album_id}#art", status_code=303)

    @router.post("/albums/{album_id}/review")
    async def save_review(request: Request, album_id: int):
        form = await checked_form(request)
        album_access(request, album_id)
        rating, error = parse_rating(form.get("rating"))
        review = str(form.get("review", "")).strip()
        if error or len(review) > MAX_REVIEW:
            raise HTTPException(status_code=400, detail=error or "Review is too long.")
        db.set_review(path, album_id, rating, review)
        return RedirectResponse(f"/albums/{album_id}#review", status_code=303)

    @router.post("/albums/{album_id}/notes")
    async def add_note(request: Request, album_id: int):
        form = await checked_form(request)
        album_access(request, album_id)
        body = str(form.get("body", "")).strip()
        if not body or len(body) > MAX_NOTE:
            raise HTTPException(status_code=400, detail=f"Notes are 1 to {MAX_NOTE} characters.")
        db.add_note(path, album_id, request.state.user["id"], body)
        return RedirectResponse(f"/albums/{album_id}#journal", status_code=303)

    @router.post("/albums/{album_id}/notes/{note_id}/delete")
    async def delete_note(request: Request, album_id: int, note_id: int):
        await checked_form(request)
        album_access(request, album_id)
        if not db.delete_note(path, album_id, note_id):
            raise HTTPException(status_code=404)
        return RedirectResponse(f"/albums/{album_id}#journal", status_code=303)

    @router.get("/albums/{album_id}/tracks")
    async def track_form(request: Request, album_id: int):
        album = album_access(request, album_id)
        ratings = db.get_track_ratings(path, album_id)
        tracks = []
        for t in release_tracks(album_id):
            r = ratings.get(t["position"])
            tracks.append(
                {**t, "rating": r["rating"] if r else None, "note": r["note"] if r else ""}
            )
        return render(request, "tracks.html", album=album, tracks=tracks, ratings=RATINGS)

    @router.post("/albums/{album_id}/tracks")
    async def save_tracks(request: Request, album_id: int):
        form = await checked_form(request)
        album_access(request, album_id)
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
        """Move to the trash (soft delete)."""
        await checked_form(request)
        album_access(request, album_id)
        db.soft_delete(path, album_id)
        return RedirectResponse("/trash", status_code=303)

    # --- Help --------------------------------------------------------------------------

    @router.get("/help")
    async def help_page(request: Request):
        parts = ["using", "admin"] if request.state.user["role"] == "admin" else ["using"]
        return render(request, "help.html", parts=[help_pages[name] for name in parts])

    # --- Trash -------------------------------------------------------------------------

    @router.get("/trash")
    async def trash_page(request: Request):
        return render(
            request,
            "trash.html",
            albums=db.list_trash(path, owner_id(request)),
            trash_days=settings.trash_days,
        )

    @router.post("/trash/{album_id}/restore")
    async def restore_album(request: Request, album_id: int):
        await checked_form(request)
        album_access(request, album_id, include_deleted=True)
        db.restore(path, album_id)
        return RedirectResponse(f"/albums/{album_id}", status_code=303)

    @router.post("/trash/{album_id}/purge")
    async def purge_one(request: Request, album_id: int):
        await checked_form(request)
        album = album_access(request, album_id, include_deleted=True)
        if album["deleted_at"] is None:
            raise HTTPException(status_code=400, detail="Move the album to the trash first.")
        purge_album(album_id)
        return RedirectResponse("/trash", status_code=303)

    @router.post("/trash/empty")
    async def empty_trash(request: Request):
        await checked_form(request)
        for album in db.list_trash(path, owner_id(request)):
            purge_album(album["id"])
        return RedirectResponse("/trash", status_code=303)

    # --- Imports -----------------------------------------------------------------------

    def sync_context(request: Request) -> dict:
        """Your Discogs link and latest collection sync, for the Import and review pages."""
        user = request.state.user
        linked = user["discogs_linked"] if user_token(user) else None
        return {
            "linked": linked,
            "run": db.latest_sync_run(path, user["id"]),
            "review_count": db.count_sync_items(path, user["id"]),
        }

    def import_page_response(request: Request, status_code: int = 200, **extra):
        context = {
            "status": import_status("collection", owner_id(request)),
            "conditions": CONDITIONS,
            "queued": jobs.pending("album") + jobs.pending("album_light"),
            "errors": {},
            **sync_context(request),
            **extra,
        }
        return render(request, "import.html", status_code, **context)

    def start_sync(request: Request, run_id: int) -> None:
        """Run a collection sync for the signed-in user with their own token."""
        user = request.state.user
        svc = discogs_for(user["id"])
        username = user["discogs_linked"]

        def work() -> None:
            try:
                sync.run_collection_sync(
                    path, svc, user["id"], username, run_id, queue_light, MAX_ALBUMS_PER_USER
                )
            except (*discogs.LOOKUP_ERRORS, ValueError) as exc:
                log.warning("Discogs sync %s failed: %s", run_id, exc)
                db.update_sync_run(path, run_id, state="error", message=import_error(exc))
            except Exception:
                log.exception("Discogs sync %s failed", run_id)
                db.update_sync_run(
                    path, run_id, state="error", message="The sync stopped unexpectedly."
                )

        if inline_jobs:
            work()
        else:
            threading.Thread(target=work, name=f"discogs-sync-{run_id}", daemon=True).start()

    sync_lock = threading.Lock()

    def own_linked_account(request: Request) -> None:
        """Private Discogs data is only read for your own linked account, never while an
        admin is viewing someone else's collection."""
        if request.state.viewing:
            raise HTTPException(
                status_code=400, detail="Switch back to your own collection to sync Discogs."
            )
        if not (request.state.user["discogs_linked"] and user_token(request.state.user)):
            raise HTTPException(
                status_code=400, detail="Add your Discogs token on your Profile page first."
            )

    @router.post("/discogs/sync")
    async def sync_collection(request: Request):
        form = await checked_form(request)
        own_linked_account(request)
        condition = form.get("condition")
        if condition not in CONDITIONS:
            return import_page_response(
                request, 422, errors={"sync_condition": "Choose a condition."}
            )
        with sync_lock:  # one sync per user, even if the button is pressed twice
            latest = db.latest_sync_run(path, request.state.user["id"])
            run_id = None
            if latest is None or latest["state"] != "running":
                run_id = db.create_sync_run(path, request.state.user["id"], condition)
        if run_id is not None:
            log.info("user %s started Discogs sync %s", request.state.user["id"], run_id)
            start_sync(request, run_id)
        return RedirectResponse("/import#discogs", status_code=303)

    @router.post("/discogs/sync/{run_id}/resume")
    async def resume_sync(request: Request, run_id: int):
        await checked_form(request)
        own_linked_account(request)
        run = db.get_sync_run(path, run_id)
        latest = db.latest_sync_run(path, request.state.user["id"])
        if run is None or latest is None or run["id"] != latest["id"]:
            raise HTTPException(status_code=404)
        with sync_lock:
            resume = db.get_sync_run(path, run_id)["state"] in ("interrupted", "error")
            if resume:
                db.update_sync_run(path, run_id, state="running", message="")
        if resume:
            start_sync(request, run_id)
        return RedirectResponse("/import#discogs", status_code=303)

    @router.get("/discogs/review")
    async def sync_review(request: Request):
        user_id = request.state.user["id"]
        return render(
            request,
            "sync_review.html",
            items=db.list_sync_items(path, user_id),
            log_rows=db.list_sync_log(path, user_id),
            folders=db.folder_names(path, user_id),
            labels=sync.LABELS,
            **sync_context(request),
        )

    def resolve(user_id: int, item, action: str) -> None:
        if item["kind"] == "conflict" and action in ("mine", "theirs"):
            if action == "theirs":
                db.set_album_field(
                    path, item["album_id"], item["field"], json.loads(item["theirs"])
                )
            db.log_sync(
                path, user_id, None, item["album_id"], f"kept {action}", sync.LABELS[item["field"]]
            )
        elif item["kind"] == "removal" and action in ("trash", "keep"):
            if action == "trash":
                db.soft_delete(path, item["album_id"])
            else:
                db.unlink_album(path, item["album_id"])
            db.log_sync(
                path, user_id, None, item["album_id"], "trashed" if action == "trash" else "kept"
            )
        else:
            raise HTTPException(status_code=400, detail="Unknown choice.")
        db.delete_sync_item(path, item["id"])

    @router.post("/discogs/review/{item_id}")
    async def resolve_item(request: Request, item_id: int):
        form = await checked_form(request)
        user_id = request.state.user["id"]
        item = db.get_sync_item(path, user_id, item_id)  # only your own items
        if item is None:
            raise HTTPException(status_code=404)
        resolve(user_id, item, str(form.get("action", "")))
        return RedirectResponse("/discogs/review", status_code=303)

    @router.post("/discogs/review-all")
    async def resolve_all(request: Request):
        """Keep yours, or take Discogs', for every conflict at once."""
        form = await checked_form(request)
        action = form.get("action")
        if action not in ("mine", "theirs"):
            raise HTTPException(status_code=400, detail="Unknown choice.")
        user_id = request.state.user["id"]
        for item in db.list_sync_items(path, user_id):
            if item["kind"] == "conflict":
                resolve(user_id, item, action)
        return RedirectResponse("/discogs/review", status_code=303)

    @router.get("/import")
    async def import_page(request: Request):
        return import_page_response(request)

    @router.post("/import")
    async def start_collection_import(request: Request):
        form = await checked_form(request)
        username = str(form.get("username", "")).strip()
        condition = form.get("condition")
        errors = {}
        if not discogs.valid_username(username):
            errors["username"] = "Enter your Discogs username."
        if condition not in CONDITIONS:
            errors["condition"] = "Choose a condition."
        if errors:
            return import_page_response(request, 422, errors=errors)
        start_import(
            "collection",
            owner_id(request),
            run_collection_import,
            acting_discogs(request),
            username,
            condition,
        )
        return RedirectResponse("/import", status_code=303)

    # --- Wishlist ----------------------------------------------------------------------

    @router.get("/wishlist")
    async def wishlist_page(request: Request):
        owner = owner_id(request)
        return render(
            request,
            "wishlist.html",
            wishes=db.list_wishes(path, owner),
            values={},
            errors={},
            status=import_status("wantlist", owner),
            conditions=CONDITIONS,
        )

    @router.post("/wishlist")
    async def add_wish(request: Request):
        form = await checked_form(request)
        data, values, errors = parse_wish(form)
        owner = owner_id(request)
        if errors:
            return render(
                request,
                "wishlist.html",
                422,
                wishes=db.list_wishes(path, owner),
                values=values,
                errors=errors,
                status=import_status("wantlist", owner),
                conditions=CONDITIONS,
            )
        queue_wish(db.create_wish(path, owner, data))
        return RedirectResponse("/wishlist", status_code=303)

    @router.post("/wishlist/import")
    async def import_wantlist(request: Request):
        form = await checked_form(request)
        username = str(form.get("username", "")).strip()
        if not discogs.valid_username(username):
            raise HTTPException(status_code=400, detail="Enter your Discogs username.")
        start_import(
            "wantlist", owner_id(request), run_wantlist_import, acting_discogs(request), username
        )
        return RedirectResponse("/wishlist", status_code=303)

    @router.post("/wishlist/{wish_id}/refresh")
    async def refresh_wish(request: Request, wish_id: int):
        await checked_form(request)
        wish = wish_access(request, wish_id)
        if wish["discogs_status"] != "pending":
            queue_wish(wish_id)
        return RedirectResponse("/wishlist", status_code=303)

    @router.post("/wishlist/{wish_id}/delete")
    async def remove_wish(request: Request, wish_id: int):
        await checked_form(request)
        wish_access(request, wish_id)
        db.delete_wish(path, wish_id)
        return RedirectResponse("/wishlist", status_code=303)

    @router.post("/wishlist/{wish_id}/got")
    async def got_wish(request: Request, wish_id: int):
        """Got it: move a wish into the collection."""
        form = await checked_form(request)
        wish = wish_access(request, wish_id)
        condition = form.get("condition")
        if condition not in CONDITIONS:
            raise HTTPException(status_code=400, detail="Choose a condition.")
        paid, error = parse_money(str(form.get("paid", "")))
        if error:
            raise HTTPException(status_code=400, detail=error)
        data = {
            "artist": wish["artist"],
            "title": wish["title"],
            "year": wish["year"],
            "label": "",
            "format": "LP",
            "condition": condition,
            "notes": wish["notes"],
            "value_cents": None,
            "purchase_cents": paid,
            "purchase_date": datetime.now(EASTERN).date().isoformat(),
        }
        pinned = wish["discogs_release_id"] if wish["pressing_locked"] else None
        album_id = db.create_album(path, wish["owner_id"], data, release_id=pinned)
        db.delete_wish(path, wish_id)
        queue_album(album_id)
        return RedirectResponse(f"/albums/{album_id}", status_code=303)

    app.include_router(router)

    # --- Admin -------------------------------------------------------------------------

    admin = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

    def admin_page(request: Request, status_code: int = 200, **extra):
        return render(
            request,
            "admin.html",
            status_code,
            users=db.list_users(path),
            links=db.list_open_links(path),
            backups=[
                {"name": b.name, "size": b.stat().st_size} for b in storage.list_backups()[:10]
            ],
            backup_hours=settings.backup_hours,
            roles=ROLES,
            queued=jobs.pending(),
            **extra,
        )

    @admin.get("")
    async def admin_home(request: Request):
        return admin_page(request)

    @admin.post("/invite")
    async def create_invite(request: Request):
        form = await checked_form(request)
        role = form.get("role")
        if role not in ROLES:
            raise HTTPException(status_code=400, detail="Choose a role.")
        token, token_hash = new_link_token()
        db.create_link(path, token_hash, "invite", request.state.user["id"], LINK_DAYS, role=role)
        return admin_page(request, new_link=link_url(request, token), new_link_kind="invite")

    def target_user(request: Request, user_id: int):
        user = db.get_user(path, user_id)
        if user is None:
            raise HTTPException(status_code=404)
        return user

    @admin.post("/users/{user_id}/reset")
    async def reset_link(request: Request, user_id: int):
        await checked_form(request)
        user = target_user(request, user_id)
        token, token_hash = new_link_token()
        db.create_link(
            path, token_hash, "reset", request.state.user["id"], LINK_DAYS, user_id=user["id"]
        )
        return admin_page(
            request,
            new_link=link_url(request, token),
            new_link_kind="reset",
            new_link_user=user["username"],
        )

    def guard_last_admin(user, becoming_inactive: bool) -> None:
        if becoming_inactive and user["role"] == "admin" and not user["disabled"]:
            if db.count_active_admins(path) <= 1:
                raise HTTPException(status_code=400, detail="Keep at least one active admin.")

    @admin.post("/users/{user_id}/disable")
    async def disable_user(request: Request, user_id: int):
        await checked_form(request)
        user = target_user(request, user_id)
        if user["id"] == request.state.user["id"]:
            raise HTTPException(status_code=400, detail="You can't disable your own account.")
        guard_last_admin(user, becoming_inactive=True)
        db.set_user_disabled(path, user_id, True)
        forget_service(user_id)  # their token is no longer used
        return RedirectResponse("/admin", status_code=303)

    @admin.post("/users/{user_id}/enable")
    async def enable_user(request: Request, user_id: int):
        await checked_form(request)
        target_user(request, user_id)
        db.set_user_disabled(path, user_id, False)
        return RedirectResponse("/admin", status_code=303)

    @admin.post("/users/{user_id}/role")
    async def change_role(request: Request, user_id: int):
        form = await checked_form(request)
        user = target_user(request, user_id)
        role = form.get("role")
        if role not in ROLES:
            raise HTTPException(status_code=400, detail="Choose a role.")
        guard_last_admin(user, becoming_inactive=role != "admin")
        db.set_user_role(path, user_id, role)
        return RedirectResponse("/admin", status_code=303)

    @admin.post("/users/{user_id}/identity")
    async def set_user_identity(request: Request, user_id: int):
        """Admin: change any account's username and email."""
        form = await checked_form(request)
        target_user(request, user_id)
        username, error = parse_username(form.get("username"))
        email, email_error = parse_email(form.get("email"))
        error = error or email_error
        if not error:
            clash = db.set_identity(path, user_id, username, email)
            if clash:
                error = f"That {clash} is already used by another account."
        if error:
            return admin_page(request, 422, identity_error=error, identity_user=user_id)
        log.info("admin %s updated username/email of user %s", request.state.user["id"], user_id)
        return RedirectResponse("/admin", status_code=303)

    @admin.post("/users/{user_id}/discogs-token/clear")
    async def clear_user_token(request: Request, user_id: int):
        """Admin: remove a user's Discogs token (e.g. after a leak). Admins never see it."""
        await checked_form(request)
        target_user(request, user_id)
        db.set_discogs_token(path, user_id, None)
        forget_service(user_id)
        log.info("admin %s cleared the Discogs token of user %s", request.state.user["id"], user_id)
        return RedirectResponse("/admin", status_code=303)

    @admin.post("/links/{link_id}/revoke")
    async def revoke(request: Request, link_id: int):
        await checked_form(request)
        db.revoke_link(path, link_id)
        return RedirectResponse("/admin", status_code=303)

    @admin.post("/view/{user_id}")
    async def view_collection(request: Request, user_id: int):
        await checked_form(request)
        target_user(request, user_id)
        request.session["view_owner"] = user_id
        return RedirectResponse("/", status_code=303)

    @admin.post("/view-mine")
    async def view_mine(request: Request):
        await checked_form(request)
        request.session.pop("view_owner", None)
        return RedirectResponse("/", status_code=303)

    @admin.post("/backup")
    async def backup_now(request: Request):
        await checked_form(request)
        await run_in_threadpool(storage.backup, settings.backup_keep)
        return RedirectResponse("/admin#backups", status_code=303)

    app.include_router(admin)
    return app


def app_from_env() -> FastAPI:
    """Factory used by uvicorn: builds the app from environment settings."""
    return create_app(Settings.from_env())
