import re

import pytest
from fastapi.testclient import TestClient

from myvinyl import db
from myvinyl.auth import hash_password
from myvinyl.config import ConfigError, Settings
from myvinyl.discogs import Enrichment, Pressing
from myvinyl.main import create_app

PASSWORD = "correct horse battery"
PASSWORD_HASH = hash_password(PASSWORD)

RELEASE = {
    "id": 28855534,
    "title": "Substance",
    "year": 2023,
    "country": "Worldwide",
    "labels": [{"name": "Factory", "catno": "Fact 200"}],
    "formats": [{"name": "Vinyl", "qty": "2", "descriptions": ["LP", "Remastered"]}],
    "genres": ["Electronic"],
    "styles": ["Synth-pop"],
    "tracklist": [{"position": "A1", "title": "Ceremony", "duration": "4:23"}],
    "extraartists": [{"name": "Peter Saville", "role": "Design"}],
    "identifiers": [{"type": "Barcode", "value": "190295371738"}],
    "community": {"have": 5000, "want": 900, "rating": {"average": 4.6, "count": 300}},
    "images": [
        {"uri": "https://evil.example/x.jpg"},
        {"uri": "https://i.discogs.com/cover.jpg", "type": "primary"},
        {
            "uri": "https://i.discogs.com/back.jpg",
            "uri150": "https://i.discogs.com/back150.jpg",
            "type": "secondary",
        },
    ],
    "notes": "<b>Remastered</b> double LP",
}


def pressing(release_id, year, price):
    return Pressing(
        release_id,
        "New Order - Substance",
        year,
        "UK",
        "Factory",
        "Fact 200",
        "Vinyl, LP",
        price,
        3,
    )


class FakeEnricher:
    """Stands in for the Discogs API so tests never touch the network."""

    def __init__(self):
        self.result = Enrichment(
            release_id=28855534,
            value_cents=2448,
            low_cents=1800,
            high_cents=7228,
            median_cents=2448,
            pressings=[
                pressing(28855534, 2023, 2448),
                pressing(28848151, 2023, 7228),
                pressing(23662, 1987, 1800),
                pressing(99, 1990, None),
            ],
            release=RELEASE,
        )
        self.calls = []
        self.error = None

    def __call__(self, artist, title, year):
        self.calls.append((artist, title, year))
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def lookup():
    return FakeEnricher()


@pytest.fixture
def client(tmp_path, lookup):
    settings = Settings(
        secret_key="x" * 48, password_hash=PASSWORD_HASH, db_path=tmp_path / "test.db"
    )
    # https base URL so the Secure session cookie is sent back.
    app = create_app(settings, enricher=lookup)
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
    assert re.search(r'stat-num">1</span><span class="stat-label">album<', html)


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
    edit_link = first_album_link(authed)
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


# --- Discogs enrichment -----------------------------------------------------------------


def first_album_link(client):
    return re.search(r'href="(/albums/\d+)"', client.get("/").text).group(1)


def edit(client, link, **fields):
    data = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "LP", "condition": "VG+"}
    data.update(fields)
    data["csrf"] = csrf_from(client, link + "/edit")
    return client.post(link, data=data, follow_redirects=False)


def test_create_runs_lookup_and_redirects_to_album(authed, lookup):
    r = add(authed, artist="New Order", title="Substance", year="2024")
    assert re.fullmatch(r"/albums/\d+", r.headers["location"])
    assert lookup.calls == [("New Order", "Substance", 2024)]
    html = authed.get("/").text
    assert "$24.48" in html and "$18.00" in html and "$72.28" in html  # value and range


def test_album_page_shows_metadata_and_pressings(authed):
    add(authed, artist="New Order", title="Substance")
    page = authed.get(first_album_link(authed)).text
    for expected in (
        "Fact 200",
        "Synth-pop",
        "Ceremony",
        "Peter Saville",
        "190295371738",
        "5000",
        "best match",
        "3 of 4 pressings",
        "Median $24.48",
    ):
        assert expected in page, expected
    # Discogs text is escaped, and only the allowlisted image host is rendered.
    assert "<b>Remastered</b>" not in page and "&lt;b&gt;Remastered" in page
    assert "https://i.discogs.com/cover.jpg" in page and "evil.example" not in page


def test_blank_fields_are_filled_but_yours_are_kept(authed):
    add(authed, artist="New Order", title="Substance")  # no year, no label
    link = first_album_link(authed)
    page = authed.get(link + "/edit").text
    assert 'value="2023"' in page and 'value="Factory"' in page

    add(authed, artist="New Order", title="Substance", year="2024", label="Mine")
    html = authed.get("/?q=Mine").text
    assert "2024" in html and "Mine" in html


def test_entered_value_is_kept_but_range_recorded(authed, lookup):
    add(authed, value="10")
    assert lookup.calls  # range and metadata are still looked up
    html = authed.get("/").text
    assert "$10.00" in html and "$18.00" in html and "$24.48" not in html


def test_no_match_and_errors_still_save(authed, lookup):
    lookup.result = None
    add(authed)
    page = authed.get(first_album_link(authed)).text
    assert "No matching vinyl release" in page

    lookup.error = OSError("offline")
    add(authed, title="Sketches of Spain")
    assert "Sketches of Spain" in authed.get("/").text


def test_manual_value_survives_refresh(authed):
    add(authed)
    link = first_album_link(authed)
    edit(authed, link, value="99")
    token = csrf_from(authed, link)
    authed.post(link + "/lookup", data={"csrf": token})
    page = authed.get(link).text
    assert "$99.00" in page and "Entered by you." in page


def test_cleared_value_is_refilled_by_refresh(authed, lookup):
    add(authed, value="5")
    link = first_album_link(authed)
    edit(authed, link, value="")
    token = csrf_from(authed, link)
    authed.post(link + "/lookup", data={"csrf": token})
    assert "$24.48" in authed.get(link).text


def test_pending_lookup_auto_refreshes_and_is_not_requeued(authed, lookup, tmp_path):
    add(authed)
    link = first_album_link(authed)
    album_id = int(link.rsplit("/", 1)[1])
    db.set_discogs_status(tmp_path / "test.db", album_id, "pending")  # simulate in-flight
    page = authed.get(link).text
    assert 'http-equiv="refresh"' in page and "Looking up" in page

    lookup.calls.clear()
    authed.post(link + "/lookup", data={"csrf": csrf_from(authed, link)})
    assert lookup.calls == []


def test_interrupted_lookups_marked_on_startup(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    album_id = db.create_album(
        path,
        {
            "artist": "A",
            "title": "B",
            "year": None,
            "label": "",
            "format": "LP",
            "condition": "M",
            "notes": "",
            "value_cents": None,
        },
    )
    db.set_discogs_status(path, album_id, "pending")
    db.init(path)
    assert db.get_album(path, album_id)["discogs_status"] == "error"


def test_csv_includes_range(authed):
    add(authed)
    text = authed.get("/export.csv").text
    assert "value_low" in text.splitlines()[0]
    assert "18.00" in text and "72.28" in text and "28855534" in text


# --- Album art, times, icons ---------------------------------------------------------------


def test_choose_album_art(authed):
    add(authed, artist="New Order", title="Substance")
    link = first_album_link(authed)
    page = authed.get(link).text
    assert "2 images from Discogs" in page and "back150.jpg" in page
    assert re.search(r'<img class="cover" src="https://i.discogs.com/cover.jpg"', page)

    token = csrf_from(authed, link)
    authed.post(link + "/cover", data={"csrf": token, "uri": "https://i.discogs.com/back.jpg"})
    page = authed.get(link).text
    assert re.search(r'<img class="cover" src="https://i.discogs.com/back.jpg"', page)


def test_cover_must_be_one_of_the_release_images(authed):
    add(authed, artist="New Order", title="Substance")
    link = first_album_link(authed)
    token = csrf_from(authed, link)
    for uri in ("https://evil.example/x.jpg", "https://i.discogs.com/not-in-release.jpg"):
        r = authed.post(link + "/cover", data={"csrf": token, "uri": uri})
        assert r.status_code == 400
    assert (
        authed.post(link + "/cover", data={"uri": "https://i.discogs.com/back.jpg"}).status_code
        == 403
    )


def test_times_shown_in_eastern(authed):
    from myvinyl.main import eastern_time

    assert eastern_time("2026-07-01 16:30:00") == "Jul 1, 2026 12:30 PM EDT"
    assert eastern_time("2026-12-01 16:30:00") == "Dec 1, 2026 11:30 AM EST"
    add(authed)
    page = authed.get(first_album_link(authed)).text
    assert re.search(r"Added \w{3} \d{1,2}, \d{4} \d{1,2}:\d{2} [AP]M E[SD]T", page)


def test_favicon_and_logo_served(client):
    assert client.get("/favicon.ico").status_code == 200
    r = client.get("/static/logo.svg")
    assert r.status_code == 200 and "<svg" in r.text
