"""Per-user Discogs tokens: encryption, profile, jobs, and admin controls."""

import base64
import io
import logging
import re
import sys
import urllib.error

import pytest
from test_app import (  # noqa: F401 (fixtures are used by name)
    PASSWORD,
    TOKEN_KEY,
    add,
    authed,
    client,
    csrf_from,
    lookup,
)
from test_multiuser import NEW_PASSWORD, second  # noqa: F401

from myvinyl import credentials, db, tokens
from myvinyl.config import ConfigError, Settings

TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789wxyz"
OTHER = "ZyXwVuTsRqPoNmLkJiHgFeDcBa9876543210abcd"


# --- TokenBox ------------------------------------------------------------------------------


def test_seal_and_open_round_trip():
    box = tokens.TokenBox(TOKEN_KEY)
    sealed = box.seal(7, TOKEN)
    assert sealed.startswith("v1:") and TOKEN not in sealed
    assert box.open(7, sealed) == TOKEN
    # A fresh nonce each time: the same token never seals to the same text.
    assert box.seal(7, TOKEN) != sealed


def test_open_fails_closed():
    box = tokens.TokenBox(TOKEN_KEY)
    sealed = box.seal(7, TOKEN)
    assert box.open(8, sealed) == ""  # copied onto another account
    assert tokens.TokenBox(bytes(32)).open(7, sealed) == ""  # different key
    raw = bytearray(base64.urlsafe_b64decode(sealed[3:]))
    for i in (0, 12, len(raw) - 1):  # nonce, ciphertext, and tag
        tampered = bytearray(raw)
        tampered[i] ^= 1
        assert box.open(7, "v1:" + base64.urlsafe_b64encode(bytes(tampered)).decode()) == ""
    for junk in (None, "", "v2:abc", "v1:!!!", "v1:" + "A" * 8, "plaintext"):
        assert box.open(7, junk) == ""


def test_key_parsing_and_generation():
    key = tokens.new_key()
    assert len(tokens.parse_key(key)) == 32
    assert tokens.new_key() != key
    for bad in ("", "short", "A" * 10, "not base64 !!!", "A" * 64):
        with pytest.raises(ValueError):
            tokens.parse_key(bad)
    with pytest.raises(ValueError):
        tokens.TokenBox(b"too short")


def test_no_secrets_in_repr():
    box = tokens.TokenBox(TOKEN_KEY)
    assert "TokenBox()" == repr(box)
    settings = Settings(secret_key="s" * 40, db_path=None, token_key=TOKEN_KEY)
    assert "token_key" not in repr(settings) and "s" * 40 not in repr(settings)


def test_token_format_and_hint():
    assert tokens.valid_token(TOKEN)
    for bad in ("", "short", "has spaces in it 1234567", "x" * 101, "<script>alert(1)</script>xx"):
        assert not tokens.valid_token(bad)
    assert tokens.hint(TOKEN) == "wxyz" and tokens.hint("") == ""


# --- Settings and the credentials helper ---------------------------------------------------


def test_token_key_required(monkeypatch):
    monkeypatch.setenv("MYVINYL_SECRET_KEY", "y" * 48)
    monkeypatch.delenv("MYVINYL_TOKEN_KEY", raising=False)
    with pytest.raises(ConfigError, match="MYVINYL_TOKEN_KEY"):
        Settings.from_env()
    monkeypatch.setenv("MYVINYL_TOKEN_KEY", "not-a-key")
    with pytest.raises(ConfigError):
        Settings.from_env()
    monkeypatch.setenv("MYVINYL_TOKEN_KEY", tokens.new_key())
    assert len(Settings.from_env().token_key) == 32


def test_old_shared_token_is_ignored_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("MYVINYL_SECRET_KEY", "y" * 48)
    monkeypatch.setenv("MYVINYL_TOKEN_KEY", tokens.new_key())
    monkeypatch.setenv("MYVINYL_DISCOGS_TOKEN", TOKEN)
    with caplog.at_level(logging.WARNING):
        settings = Settings.from_env()
    assert "no longer used" in caplog.text and TOKEN not in caplog.text
    assert not hasattr(settings, "discogs_token")


def test_credentials_prints_token_key(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["credentials", "--token-key"])
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    assert credentials.main() == 0
    key = re.fullmatch(r"MYVINYL_TOKEN_KEY=(\S+)\n", out.getvalue()).group(1)
    assert len(tokens.parse_key(key)) == 32


# --- Profile page ---------------------------------------------------------------------------


def save_token(client, token=TOKEN, password=PASSWORD):
    csrf = csrf_from(client, "/account")
    return client.post(
        "/account/discogs-token", data={"csrf": csrf, "token": token, "current": password}
    )


def stored(tmp_path, username="admin"):
    return db.get_user_by_name(tmp_path / "test.db", username)["discogs_token"]


def test_save_token_encrypted_and_only_hint_shown(authed, lookup, tmp_path):
    page = authed.get("/account").text
    assert "No token yet" in page

    r = save_token(authed)
    assert r.status_code == 200 and "Token saved" in r.text
    assert "wxyz" in r.text and TOKEN not in r.text
    sealed = stored(tmp_path)
    assert sealed.startswith("v1:") and TOKEN not in sealed
    assert tokens.TokenBox(TOKEN_KEY).open(1, sealed) == TOKEN
    assert lookup.used_tokens[-1] == TOKEN  # checked with Discogs using that token
    # The Discogs username was blank, so it was filled from the token's owner.
    assert db.get_user_by_name(tmp_path / "test.db", "admin")["discogs_username"] == "discogsfan"
    assert TOKEN not in authed.get("/account").text


def test_save_token_needs_password_and_valid_token(authed, lookup, tmp_path):
    r = save_token(authed, password="wrong password!")
    assert r.status_code == 422 and "current password" in r.text
    r = save_token(authed, token="bad token!")
    assert r.status_code == 422 and "look like a Discogs token" in r.text
    assert "bad token!" not in r.text  # never echoed back
    r = authed.post("/account/discogs-token", data={"token": TOKEN, "current": PASSWORD})
    assert r.status_code == 403  # no CSRF token
    assert stored(tmp_path) is None


def test_save_token_rejected_or_unreachable(authed, lookup, tmp_path):
    lookup.identity_error = urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
    r = save_token(authed)
    assert r.status_code == 422 and "accept this token" in r.text
    lookup.identity_error = urllib.error.HTTPError("u", 429, "Too Many", {}, None)
    assert "check the token right now" in save_token(authed).text
    lookup.identity_error = urllib.error.URLError("down")
    assert "be reached to check" in save_token(authed).text
    assert stored(tmp_path) is None  # fail closed: nothing stored unverified


def test_replace_and_remove_token(authed, tmp_path):
    save_token(authed)
    save_token(authed, token=OTHER)
    assert tokens.TokenBox(TOKEN_KEY).open(1, stored(tmp_path)) == OTHER

    csrf = csrf_from(authed, "/account")
    r = authed.post("/account/discogs-token/delete", data={"csrf": csrf, "current": "nope"})
    assert r.status_code == 422 and stored(tmp_path)
    r = authed.post("/account/discogs-token/delete", data={"csrf": csrf, "current": PASSWORD})
    assert r.status_code == 200 and "Token removed" in r.text
    assert stored(tmp_path) is None


# --- Which token Discogs work uses ----------------------------------------------------------


def test_lookups_use_the_collection_owners_token(authed, second, lookup, tmp_path):
    add(authed, artist="New Order", title="Substance")
    assert lookup.used_tokens[-1] == ""  # no token yet: anonymous

    save_token(authed)
    lookup.used_tokens.clear()
    add(authed, artist="Joy Division", title="Closer")
    assert lookup.used_tokens and set(lookup.used_tokens) == {TOKEN}

    # Bob has no token: his lookups stay anonymous and never borrow the admin's.
    lookup.used_tokens.clear()
    add(second, artist="Bob", title="Record")
    assert lookup.used_tokens and set(lookup.used_tokens) == {""}

    # Barcode search uses the owner's token too.
    lookup.used_tokens.clear()
    lookup.identifier_matches = []
    add(authed, identifier="12345")
    assert lookup.used_tokens == [TOKEN]


def test_undecryptable_token_falls_back_to_anonymous(authed, lookup, tmp_path):
    save_token(authed)
    # Simulate a changed key: a value sealed under another key can't be opened.
    other_box = tokens.TokenBox(bytes(32))
    db.set_discogs_token(tmp_path / "test.db", 1, other_box.seal(1, TOKEN))
    lookup.used_tokens.clear()
    add(authed, artist="New Order", title="Substance")
    assert set(lookup.used_tokens) == {""}
    assert "No token yet" in authed.get("/account").text


# --- Admin ----------------------------------------------------------------------------------


def test_admin_sees_set_and_clears_but_never_sees_token(authed, second, tmp_path, caplog):
    csrf = csrf_from(second, "/account")
    second.post(
        "/account/discogs-token", data={"csrf": csrf, "token": OTHER, "current": NEW_PASSWORD}
    )
    page = authed.get("/admin").text
    assert OTHER not in page and OTHER[-4:] not in page
    assert "Clear the Discogs token of bob" in page

    bob = db.get_user_by_name(tmp_path / "test.db", "bob")
    # Members can't use the admin route (404, like every admin route).
    assert (
        second.post(
            f"/admin/users/{bob['id']}/discogs-token/clear", data={"csrf": csrf}
        ).status_code
        == 404
    )
    csrf_admin = csrf_from(authed, "/admin")
    with caplog.at_level(logging.INFO):
        r = authed.post(f"/admin/users/{bob['id']}/discogs-token/clear", data={"csrf": csrf_admin})
    assert r.status_code == 200 and stored(tmp_path, "bob") is None
    assert f"cleared the Discogs token of user {bob['id']}" in caplog.text
    assert OTHER not in caplog.text


def test_token_never_logged(authed, lookup, caplog):
    with caplog.at_level(logging.DEBUG):
        save_token(authed)
        lookup.identity_error = urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
        save_token(authed, token=OTHER)
    assert TOKEN not in caplog.text and OTHER not in caplog.text


# --- Security review follow-ups -------------------------------------------------------------


def test_wrong_current_passwords_are_rate_limited(authed, tmp_path):
    for _ in range(5):
        assert save_token(authed, password="not my password").status_code == 422
    # Locked: further attempts are refused without checking, and the account was signed
    # out everywhere, so a stolen session can't keep guessing.
    assert authed.get("/", follow_redirects=False).status_code == 303
    login_again = csrf_from(authed, "/login")
    authed.post("/login", data={"csrf": login_again, "username": "admin", "password": PASSWORD})
    assert save_token(authed).status_code == 429
    assert stored(tmp_path) is None


def bob_saves_token(second):
    csrf = csrf_from(second, "/account")
    r = second.post(
        "/account/discogs-token", data={"csrf": csrf, "token": OTHER, "current": NEW_PASSWORD}
    )
    assert r.status_code == 200


def test_admin_viewing_a_collection_never_uses_that_users_token(authed, second, lookup, tmp_path):
    bob_saves_token(second)
    save_token(authed)
    bob = db.get_user_by_name(tmp_path / "test.db", "bob")
    authed.post(f"/admin/view/{bob['id']}", data={"csrf": csrf_from(authed, "/admin")})

    # An import started while viewing bob runs with the admin's own token.
    lookup.used_tokens.clear()
    csrf = csrf_from(authed, "/import")
    authed.post("/import", data={"csrf": csrf, "username": "bobsdiscogs", "condition": "VG+"})
    assert lookup.used_tokens == [TOKEN]

    # So does a barcode search.
    lookup.used_tokens.clear()
    lookup.identifier_matches = []
    add(authed, identifier="12345")
    assert lookup.used_tokens == [TOKEN]


def test_disabled_users_token_is_not_used(authed, second, lookup, tmp_path):
    bob_saves_token(second)
    bob = db.get_user_by_name(tmp_path / "test.db", "bob")
    csrf = csrf_from(authed, "/admin")
    authed.post(f"/admin/users/{bob['id']}/disable", data={"csrf": csrf})
    authed.post(f"/admin/view/{bob['id']}", data={"csrf": csrf})
    lookup.used_tokens.clear()
    add(authed, artist="New Order", title="Substance")  # bob's album, looked up in a job
    assert OTHER not in lookup.used_tokens
    # Disabled owners' albums and wishes are left out of the scheduled refresh.
    authed.post("/admin/view-mine", data={"csrf": csrf_from(authed, "/")})
    add(authed, artist="Admin", title="Mine")
    path = tmp_path / "test.db"
    with db.connect(path) as conn:
        conn.execute("UPDATE albums SET discogs_checked_at = '2000-01-01 00:00:00'")
        mine = conn.execute("SELECT id FROM albums WHERE title = 'Mine'").fetchone()["id"]
    assert db.due_for_refresh(path, 7, 50) == [mine]
