import re

import pytest
from fastapi.testclient import TestClient

from myvinyl.auth import hash_password
from myvinyl.config import ConfigError, Settings
from myvinyl.discogs import ValueLookup
from myvinyl.main import create_app

PASSWORD = "correct horse battery"
PASSWORD_HASH = hash_password(PASSWORD)


class FakeLookup:
    """Stands in for the Discogs API so tests never touch the network."""

    def __init__(self):
        self.result = ValueLookup(release_id=28855534, value_cents=2448)
        self.calls = []

    def __call__(self, artist, title, year):
        self.calls.append((artist, title, year))
        return self.result


@pytest.fixture
def lookup():
    return FakeLookup()


@pytest.fixture
def client(tmp_path, lookup):
    settings = Settings(
        secret_key="x" * 48, password_hash=PASSWORD_HASH, db_path=tmp_path / "test.db"
    )
    # https base URL so the Secure session cookie is sent back.
    app = create_app(settings, value_lookup=lookup)
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def csrf_from(client, path):
    html = client.get(path).text
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def login(client, password=PASSWORD):
    token = csrf_from(client, "/login")
    return client.post("/login", data={"csrf": token, "password": password}, follow_redirects=False)


@pytest.fixture
def authed(client):
    assert login(client).status_code == 303
    return client


def add(client, **fields):
    data = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "LP", "condition": "VG+"}
    data.update(fields)
    data["csrf"] = csrf_from(client, "/albums/new")
    return client.post("/albums", data=data, follow_redirects=False)


# --- Auth --------------------------------------------------------------------------------


def test_pages_require_login(client):
    for path in ("/", "/albums/new", "/export.csv"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"


def test_wrong_password_rejected(client):
    assert login(client, "wrong").status_code == 401
    assert client.get("/", follow_redirects=False).status_code == 303


def test_login_rate_limited(client):
    for _ in range(5):
        login(client, "wrong")
    # Even the correct password is refused while blocked.
    assert login(client).status_code == 429


def test_session_cookie_flags(client):
    r = login(client)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie


def test_logout(authed):
    token = csrf_from(authed, "/")
    authed.post("/logout", data={"csrf": token})
    assert authed.get("/", follow_redirects=False).status_code == 303


def test_settings_fail_closed(monkeypatch):
    monkeypatch.delenv("MYVINYL_SECRET_KEY", raising=False)
    monkeypatch.setenv("MYVINYL_PASSWORD_HASH", PASSWORD_HASH)
    with pytest.raises(ConfigError):
        Settings.from_env()
    monkeypatch.setenv("MYVINYL_SECRET_KEY", "y" * 48)
    monkeypatch.setenv("MYVINYL_PASSWORD_HASH", "plaintext")
    with pytest.raises(ConfigError):
        Settings.from_env()


# --- CSRF and headers --------------------------------------------------------------------


def test_post_without_csrf_rejected(authed):
    r = authed.post("/albums", data={"artist": "A", "title": "B", "format": "LP", "condition": "M"})
    assert r.status_code == 403


def test_security_headers(client):
    r = client.get("/login")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


# --- Albums ------------------------------------------------------------------------------


def test_create_and_list(authed):
    assert add(authed, year="1959", value="42.50").status_code == 303
    html = authed.get("/").text
    assert "Kind of Blue" in html
    assert "$42.50" in html
    assert "1 album " in html


def test_validation_errors(authed):
    r = add(authed, artist="", year="1700", value="-5", format="8-track")
    assert r.status_code == 422
    assert "Required." in r.text
    assert "Enter a year" in r.text
    assert "Enter an amount" in r.text
    assert "Choose a format." in r.text


def test_output_is_escaped(authed):
    add(authed, artist="<script>alert(1)</script>")
    html = authed.get("/").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_search_and_sort(authed):
    add(authed, artist="Coltrane", title="Blue Train", value="30")
    add(authed, artist="Bjork", title="Post", value="80")
    html = authed.get("/?q=blue").text
    assert "Blue Train" in html and "Post" not in html
    # Wildcards are literal, not LIKE patterns.
    assert "No albums match" in authed.get("/?q=%25").text

    html = authed.get("/?sort=value&dir=desc").text
    assert html.index("Post") < html.index("Blue Train")
    # Unknown sort keys fall back safely.
    assert authed.get("/?sort=id;DROP TABLE albums").status_code == 200


def test_edit_and_delete(authed):
    add(authed)
    edit_link = re.search(r'href="(/albums/\d+)/edit"', authed.get("/").text).group(1)
    token = csrf_from(authed, f"{edit_link}/edit")
    r = authed.post(
        edit_link,
        data={
            "csrf": token,
            "artist": "Miles Davis",
            "title": "Bitches Brew",
            "format": "2xLP",
            "condition": "NM",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "Bitches Brew" in authed.get("/").text

    authed.post(f"{edit_link}/delete", data={"csrf": token})
    assert "No albums yet" in authed.get("/").text
    assert authed.get(f"{edit_link}/edit").status_code == 404


def test_csv_export_neutralizes_formulas(authed):
    add(authed, artist='=HYPERLINK("http://evil")', value="12")
    r = authed.get("/export.csv")
    assert r.headers["content-type"].startswith("text/csv")
    assert "'=HYPERLINK" in r.text
    assert "12.00" in r.text


# --- Discogs value lookup ----------------------------------------------------------------


def first_edit_link(client):
    return re.search(r'href="(/albums/\d+)/edit"', client.get("/").text).group(1)


def test_blank_value_is_looked_up_on_create(authed, lookup):
    add(authed, artist="New Order", title="Substance", year="2023")
    assert lookup.calls == [("New Order", "Substance", 2023)]
    assert "$24.48" in authed.get("/").text
    edit = authed.get(first_edit_link(authed) + "/edit").text
    assert "discogs.com/release/28855534" in edit


def test_entered_value_skips_lookup(authed, lookup):
    add(authed, value="10")
    assert lookup.calls == []
    assert "$10.00" in authed.get("/").text


def test_failed_lookup_still_saves(authed, lookup):
    lookup.result = None
    assert add(authed).status_code == 303
    assert "Kind of Blue" in authed.get("/").text


def test_manual_edit_overrides_discogs_value(authed):
    add(authed)
    link = first_edit_link(authed)
    token = csrf_from(authed, link + "/edit")
    data = {
        "csrf": token,
        "artist": "Miles Davis",
        "title": "Kind of Blue",
        "format": "LP",
        "condition": "VG+",
        "value": "99",
    }
    authed.post(link, data=data)
    edit = authed.get(link + "/edit").text
    assert "$99.00" in authed.get("/").text
    assert "discogs.com/release" not in edit


def test_lookup_button(authed, lookup):
    add(authed, value="5")
    link = first_edit_link(authed)
    token = csrf_from(authed, link + "/edit")
    r = authed.post(link + "/lookup", data={"csrf": token})
    assert "Value updated from Discogs." in r.text
    assert "$24.48" in authed.get("/").text

    lookup.result = None
    r = authed.post(link + "/lookup", data={"csrf": token})
    assert "No Discogs match" in r.text
