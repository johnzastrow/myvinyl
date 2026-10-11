"""Error paths and less-travelled routes: Discogs failures, imports, links, wishlist, admin."""

import re
import urllib.error

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_app import (  # noqa: F401 (fixtures are used by name)
    PASSWORD_HASH,
    TOKEN_KEY,
    add,
    authed,
    client,
    csrf_from,
    first_album_link,
    lookup,
)
from test_multiuser import album_id_of, make_link

from myvinyl import db, discogs, main, tokens
from myvinyl.config import ConfigError, Settings

HTTP = urllib.error.HTTPError


def http_error(code):
    return HTTP("https://api.discogs.com/x", code, "error", {}, None)


def album_row(tmp_path, link):
    return db.get_album(tmp_path / "test.db", album_id_of(link))


# --- App construction -----------------------------------------------------------------------


def test_create_app_fails_closed(tmp_path):
    with pytest.raises(ConfigError, match="TOKEN_KEY"):
        main.create_app(Settings(secret_key="x" * 48, db_path=tmp_path / "a.db"))
    with pytest.raises(ConfigError, match="PASSWORD_HASH"):
        main.create_app(
            Settings(secret_key="x" * 48, db_path=tmp_path / "a.db", token_key=TOKEN_KEY)
        )


def test_app_from_env_builds_real_discogs_access(monkeypatch, tmp_path):
    monkeypatch.setenv("MYVINYL_SECRET_KEY", "y" * 48)
    monkeypatch.setenv("MYVINYL_PASSWORD_HASH", PASSWORD_HASH)
    monkeypatch.setenv("MYVINYL_TOKEN_KEY", tokens.new_key())
    monkeypatch.setenv("MYVINYL_DB", str(tmp_path / "env.db"))
    app = main.app_from_env()
    assert isinstance(app, FastAPI)
    with TestClient(app, base_url="https://testserver") as c:  # starts and stops the workers
        assert c.get("/healthz").text == "ok"


def test_eastern_time_handles_bad_input():
    assert main.eastern_time(None) == "" and main.eastern_time("") == ""
    assert main.eastern_time("not a time") == "not a time"
    assert main.eastern_date(None) == ""


# --- Background jobs: Discogs failures ------------------------------------------------------


def test_album_lookup_errors_are_recorded(authed, lookup, tmp_path):
    lookup.error = urllib.error.URLError("down")
    link = add(authed, artist="A", title="B").headers["location"]
    assert album_row(tmp_path, link)["discogs_status"] == "error"

    lookup.error = RuntimeError("unexpected")  # a bug, not a network problem
    link = add(authed, artist="C", title="D").headers["location"]
    assert album_row(tmp_path, link)["discogs_status"] == "error"

    lookup.error, lookup.result = None, None
    link = add(authed, artist="E", title="F").headers["location"]
    assert album_row(tmp_path, link)["discogs_status"] == "none"


def test_cover_download_failure_keeps_no_file(authed, lookup, tmp_path):
    lookup.image_error = True
    link = add(authed, artist="New Order", title="Substance").headers["location"]
    assert album_row(tmp_path, link)["cover_file"] is None
    assert authed.get(f"/covers/{album_id_of(link)}").status_code == 404


def add_wish(client, **fields):
    token = csrf_from(client, "/wishlist")
    data = {"csrf": token, "artist": "Can", "title": "Tago Mago", **fields}
    return client.post("/wishlist", data=data, follow_redirects=False)


def first_wish(client):
    return re.search(r"/wishlist/(\d+)/got", client.get("/wishlist").text).group(1)


def test_wish_price_check_errors(authed, lookup, tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(lookup, "price_wish", broken)
    add_wish(authed)
    wish = db.get_wish(tmp_path / "test.db", int(first_wish(authed)))
    assert wish["discogs_status"] == "error"

    monkeypatch.setattr(lookup, "price_wish", lambda *a, **k: None)
    token = csrf_from(authed, "/wishlist")
    authed.post(f"/wishlist/{wish['id']}/refresh", data={"csrf": token})
    assert db.get_wish(tmp_path / "test.db", wish["id"])["discogs_status"] == "none"


def test_hourly_refresh_queues_stale_items(authed, lookup, tmp_path):
    add(authed, artist="A", title="B")
    add_wish(authed)
    with db.connect(tmp_path / "test.db") as conn:
        conn.execute("UPDATE albums SET discogs_checked_at = '2000-01-01 00:00:00'")
        conn.execute("UPDATE wishlist SET checked_at = '2000-01-01 00:00:00'")
    assert "queued 1 albums and 1 wishes" in authed.app.state.hourly()


# --- Imports --------------------------------------------------------------------------------


def failing(exc):
    def gen(username):
        raise exc
        yield  # pragma: no cover

    return gen


@pytest.mark.parametrize(
    ("exc", "message"),
    [
        (http_error(401), "refused access"),
        (http_error(404), "no user by that name"),
        (urllib.error.URLError("down"), "could not be reached"),
        (RuntimeError("bug"), "stopped unexpectedly"),
    ],
)
def test_collection_import_errors(authed, lookup, monkeypatch, exc, message):
    monkeypatch.setattr(lookup, "collection", failing(exc))
    token = csrf_from(authed, "/import")
    authed.post("/import", data={"csrf": token, "username": "fan", "condition": "VG+"})
    assert message in authed.get("/import").text


def test_collection_import_validation_and_size_cap(authed, lookup, monkeypatch):
    token = csrf_from(authed, "/import")
    r = authed.post("/import", data={"csrf": token, "username": "bad name!", "condition": "?"})
    assert r.status_code == 422 and "Enter your Discogs username" in r.text

    item = {"release_id": 5, "artist": "A", "title": "B", "year": 3000, "label": "",
            "format": "LP", "rating": None}  # fmt: skip
    lookup.collection_items = [item, {**item, "release_id": 6}]
    monkeypatch.setattr(main, "MAX_ALBUMS_PER_USER", 1)
    authed.post("/import", data={"csrf": token, "username": "fan", "condition": "VG+"})
    assert "size limit" in authed.get("/import").text


@pytest.mark.parametrize(
    ("exc", "message"),
    [(http_error(403), "refused access"), (RuntimeError("bug"), "stopped unexpectedly")],
)
def test_wantlist_import_errors(authed, lookup, monkeypatch, exc, message):
    monkeypatch.setattr(lookup, "wantlist", failing(exc))
    token = csrf_from(authed, "/wishlist")
    authed.post("/wishlist/import", data={"csrf": token, "username": "fan"})
    assert message in authed.get("/wishlist").text
    r = authed.post("/wishlist/import", data={"csrf": token, "username": "bad name!"})
    assert r.status_code == 400


def test_barcode_search_unreachable(authed, lookup, monkeypatch):
    def down(identifier):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(lookup, "find_by_identifier", down)
    r = add(authed, identifier="12345")
    assert r.status_code == 422 and "could not be reached" in r.text


# --- Wishlist routes ------------------------------------------------------------------------


def test_wishlist_refresh_delete_and_got_validation(authed, tmp_path):
    add_wish(authed)
    wish_id = first_wish(authed)
    token = csrf_from(authed, "/wishlist")
    assert (
        authed.post(
            f"/wishlist/{wish_id}/got", data={"csrf": token, "condition": "??", "paid": ""}
        ).status_code
        == 400
    )
    assert (
        authed.post(
            f"/wishlist/{wish_id}/got", data={"csrf": token, "condition": "NM", "paid": "abc"}
        ).status_code
        == 400
    )
    r = authed.post(f"/wishlist/{wish_id}/delete", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303 and "Tago Mago" not in authed.get("/wishlist").text


# --- Notes, uploads ---------------------------------------------------------------------------


def test_delete_missing_note_is_404(authed):
    add(authed)
    link = first_album_link(authed)
    token = csrf_from(authed, link)
    assert authed.post(f"{link}/notes/999/delete", data={"csrf": token}).status_code == 404


def test_upload_without_a_file_is_refused(authed):
    add(authed)
    link = first_album_link(authed)
    token = csrf_from(authed, link)
    r = authed.post(
        link + "/cover/upload", data={"csrf": token}, files={"other": ("x.txt", b"x", "text/plain")}
    )
    assert r.status_code == 400


# --- Links and admin --------------------------------------------------------------------------


def test_link_errors(authed, client, tmp_path):
    link = make_link(authed)
    other = TestClient(authed.app, base_url="https://testserver")
    token = csrf_from(other, link)
    # Unknown link: 404, and repeated misses from one IP get blocked.
    for _ in range(5):
        r = other.post("/link/not-a-real-token", data={"csrf": token})
        assert r.status_code == 404
    assert other.post("/link/not-a-real-token", data={"csrf": token}).status_code == 429


def test_invite_rejects_email_in_use(authed, tmp_path):
    db.set_identity(tmp_path / "test.db", 1, "admin", "me@example.com")
    link = make_link(authed)
    other = TestClient(authed.app, base_url="https://testserver")
    token = csrf_from(other, link)
    pw = "a long enough password"
    r = other.post(
        link,
        data={
            "csrf": token,
            "username": "carol",
            "email": "ME@example.com",
            "password": pw,
            "confirm": pw,
        },  # fmt: skip
    )
    assert r.status_code == 422 and "already used" in r.text


def test_admin_validation_and_link_revoke(authed, tmp_path):
    token = csrf_from(authed, "/admin")
    assert authed.post("/admin/invite", data={"csrf": token, "role": "king"}).status_code == 400
    assert authed.post("/admin/users/999/enable", data={"csrf": token}).status_code == 404
    assert (
        authed.post("/admin/users/1/role", data={"csrf": token, "role": "king"}).status_code == 400
    )
    make_link(authed)
    link_id = db.list_open_links(tmp_path / "test.db")[0]["id"]
    authed.post(f"/admin/links/{link_id}/revoke", data={"csrf": token})
    assert db.list_open_links(tmp_path / "test.db") == []


def test_disable_then_enable(authed, tmp_path):
    link = make_link(authed)
    other = TestClient(authed.app, base_url="https://testserver")
    pw = "a long enough password"
    other.post(
        link,
        data={"csrf": csrf_from(other, link), "username": "dave", "password": pw, "confirm": pw},
    )
    dave = db.get_user_by_name(tmp_path / "test.db", "dave")
    token = csrf_from(authed, "/admin")
    authed.post(f"/admin/users/{dave['id']}/disable", data={"csrf": token})
    assert db.get_user(tmp_path / "test.db", dave["id"])["disabled"] == 1
    authed.post(f"/admin/users/{dave['id']}/enable", data={"csrf": token})
    assert db.get_user(tmp_path / "test.db", dave["id"])["disabled"] == 0


def test_discogs_service_wrappers(monkeypatch):
    calls = []
    for name in ("enrich", "find_by_identifier", "collection", "wantlist", "price_wish"):
        monkeypatch.setattr(
            discogs,
            name,
            lambda *a, _n=name, **k: (
                calls.append(_n) or ([] if _n in ("collection", "wantlist") else None)
            ),
        )
    monkeypatch.setattr(discogs, "fetch_image", lambda url: calls.append("fetch_image"))
    svc = discogs.DiscogsService(discogs.DiscogsClient(), has_token=False)
    svc.enrich("a", "b", None)
    svc.find_by_identifier("1")
    list(svc.collection("u"))
    list(svc.wantlist("u"))
    svc.price_wish("a", "b", None)
    svc.fetch_image("https://i.discogs.com/x.jpg")
    assert calls == ["enrich", "find_by_identifier", "collection", "wantlist", "price_wish",
                     "fetch_image"]  # fmt: skip
