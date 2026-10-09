"""myvinyl web application."""

import csv
import hmac
import io
import logging
import secrets
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from . import db, discogs
from .albums import CONDITIONS, FORMATS, album_to_values, format_money, parse_album
from .auth import LoginLimiter, verify_password
from .config import Settings

PKG_DIR = Path(__file__).parent
SESSION_MAX_AGE = 12 * 60 * 60  # 12 hours
MAX_QUERY_LEN = 200

log = logging.getLogger("myvinyl")

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self'; img-src 'self'; script-src 'none'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}


# (artist, title, year) -> Discogs match, or None. Injectable so tests avoid the network.
ValueLookupFn = Callable[[str, str, int | None], discogs.ValueLookup | None]


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


def create_app(
    settings: Settings,
    value_lookup: ValueLookupFn = discogs.lookup_value,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=PKG_DIR / "templates")
    templates.env.filters["money"] = format_money
    limiter = LoginLimiter()
    db.init(settings.db_path)

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
        return templates.TemplateResponse(request, name, context, status_code=status_code)

    async def fetch_value(album_id: int, artist: str, title: str, year: int | None) -> bool:
        """Look up the street value on Discogs (blocking I/O, so off the event loop)."""
        result = await run_in_threadpool(value_lookup, artist, title, year)
        if result is None:
            return False
        db.set_discogs_value(settings.db_path, album_id, result.release_id, result.value_cents)
        return True

    def album_form(
        request, album_id=None, values=None, errors=None, status_code=200, album=None, notice=""
    ):
        return render(
            request,
            "form.html",
            status_code=status_code,
            album_id=album_id,
            album=album,
            notice=notice,
            values=values or {"format": "LP", "condition": "VG+"},
            errors=errors or {},
            formats=FORMATS,
            conditions=CONDITIONS,
        )

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
        count, value = db.totals(settings.db_path)
        return render(
            request,
            "index.html",
            albums=db.list_albums(settings.db_path, q, sort, direction),
            q=q,
            sort=sort,
            direction=direction,
            count=count,
            total_value=value,
        )

    @router.get("/albums/new")
    async def new_album(request: Request):
        return album_form(request)

    @router.post("/albums")
    async def create_album(request: Request):
        data, values, errors = parse_album(await checked_form(request))
        if errors:
            return album_form(request, values=values, errors=errors, status_code=422)
        album_id = db.create_album(settings.db_path, data)
        if data["value_cents"] is None:
            # No value entered: fill it automatically from the first Discogs match.
            await fetch_value(album_id, data["artist"], data["title"], data["year"])
        else:
            db.mark_value_manual(settings.db_path, album_id)
        return RedirectResponse("/", status_code=303)

    @router.get("/albums/{album_id}/edit")
    async def edit_album(request: Request, album_id: int):
        album = db.get_album(settings.db_path, album_id)
        if album is None:
            raise HTTPException(status_code=404, detail="Album not found.")
        notice = {
            "found": "Value updated from Discogs.",
            "none": "No Discogs match found, or the lookup failed.",
        }.get(request.query_params.get("lookup", ""), "")
        return album_form(
            request, album_id=album_id, values=album_to_values(album), album=album, notice=notice
        )

    @router.post("/albums/{album_id}")
    async def update_album(request: Request, album_id: int):
        data, values, errors = parse_album(await checked_form(request))
        album = db.get_album(settings.db_path, album_id)
        if album is None:
            raise HTTPException(status_code=404, detail="Album not found.")
        if errors:
            return album_form(request, album_id, values, errors, status_code=422, album=album)
        db.update_album(settings.db_path, album_id, data)
        if data["value_cents"] != album["value_cents"]:
            db.mark_value_manual(settings.db_path, album_id)  # user overrode the value
        return RedirectResponse("/", status_code=303)

    @router.post("/albums/{album_id}/lookup")
    async def lookup_album_value(request: Request, album_id: int):
        await checked_form(request)
        album = db.get_album(settings.db_path, album_id)
        if album is None:
            raise HTTPException(status_code=404, detail="Album not found.")
        found = await fetch_value(album_id, album["artist"], album["title"], album["year"])
        result = "found" if found else "none"
        return RedirectResponse(f"/albums/{album_id}/edit?lookup={result}", status_code=303)

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
        header = ["artist", "title", "year", "label", "format", "condition", "notes", "value"]
        writer.writerow(header)
        for a in db.list_albums(settings.db_path):
            value = "" if a["value_cents"] is None else f"{a['value_cents'] / 100:.2f}"
            row = [a[c] for c in header[:-1]] + [value]
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
