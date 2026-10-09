"""Regression tests for findings from the security review (see SECURITY.md)."""

import logging
import urllib.error

import pytest
from fastapi.testclient import TestClient
from test_app import add, authed, client, csrf_from, login, lookup  # noqa: F401

from myvinyl import discogs, main
from myvinyl.__main__ import RedactLinkTokens


def test_sign_out_everywhere_kills_other_sessions(authed):
    other = TestClient(authed.app, base_url="https://testserver")
    assert login(other).status_code == 303
    authed.post("/account/sign-out-everywhere", data={"csrf": csrf_from(authed, "/account")})
    assert other.get("/", follow_redirects=False).status_code == 303
    assert authed.get("/", follow_redirects=False).status_code == 303


def test_image_download_refuses_redirects(monkeypatch):
    def fake_open(req, timeout):
        # What the no-redirect opener does when the server answers 302.
        raise urllib.error.HTTPError(req.full_url, 302, "redirect refused", {}, None)

    monkeypatch.setattr(discogs._NO_REDIRECTS, "open", fake_open)
    with pytest.raises(urllib.error.HTTPError):
        discogs.fetch_image("https://i.discogs.com/x.jpg")
    for bad in (
        "http://i.discogs.com/x.jpg",
        "https://evil.com/x.jpg",
        "https://i.discogs.com:8443/x.jpg",
        "https://i.discogs.com.evil.com/x.jpg",
    ):
        with pytest.raises(ValueError):
            discogs.fetch_image(bad)
    handler = discogs._RefuseRedirects()
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(
            type("R", (), {"full_url": "https://i.discogs.com/a"})(), None, 302, "", {}, "http://x"
        )


def test_access_log_redacts_link_tokens():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4", "GET", "/link/abcDEF123_secret?x=1", "1.1", 200),
        None,
    )
    RedactLinkTokens().filter(record)
    assert "abcDEF123_secret" not in record.getMessage()
    assert "/link/[redacted]" in record.getMessage()


def test_album_cap_per_account(authed, monkeypatch):
    monkeypatch.setattr(main, "MAX_ALBUMS_PER_USER", 1)  # read at request time
    assert add(authed).status_code == 303
    assert add(authed, title="One too many").status_code == 400
