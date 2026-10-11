"""The in-app user guide: rendering, admin-only section, and links from the pages."""

import re

from test_app import add, authed, client, first_album_link, lookup  # noqa: F401 (fixtures)
from test_multiuser import second  # noqa: F401

from myvinyl import guide

PAGES = [
    "/",
    "/albums/new",
    "/stats",
    "/wishlist",
    "/trash",
    "/import",
    "/discogs/review",
    "/account",
]


def ids_on(html):
    return set(re.findall(r'id="([^"]+)"', html))


def test_render_escapes_html_and_numbers_anchors():
    part = guide.render("## Notes\n\n<script>alert(1)</script>\n\n## Notes\n\n### Sub, part!\n")
    assert "<script>" not in part.html
    assert "&lt;script&gt;" in part.html
    assert 'id="notes"' in part.html and 'id="notes-1"' in part.html
    assert 'id="sub-part"' in part.html
    assert part.contents == [("notes", "Notes"), ("notes-1", "Notes")]


def test_guide_files_load_with_unique_anchors():
    parts = guide.load()
    assert set(parts) == {"using", "admin"}
    assert ("adding-albums", "Adding albums") in parts["using"].contents
    assert "<table>" in parts["using"].html  # tables are enabled


def test_member_sees_guide_without_admin_section(second):
    r = second.get("/help")
    assert r.status_code == 200
    assert "Adding albums" in r.text and "Your Discogs collection" in r.text
    assert "Administering the site" not in r.text
    assert "Invite someone" not in r.text
    assert 'href="/help"' in r.text  # nav link


def test_admin_sees_admin_section(authed):
    r = authed.get("/help")
    assert "Administering the site" in r.text
    assert 'id="managing-users"' in r.text


def test_help_needs_sign_in(client):
    r = client.get("/help", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_every_page_help_link_has_a_target(authed, lookup):
    add(authed, artist="Talk Talk", title="Spirit of Eden")
    pages = [*PAGES, "/admin", first_album_link(authed)]
    targets = ids_on(authed.get("/help").text)
    for page in pages:
        html = authed.get(page).text
        anchors = re.findall(r'href="/help#([^"]+)">Help for this page', html)
        assert len(anchors) == 1, page
        assert anchors[0] in targets, (page, anchors[0])


def test_help_page_has_no_page_help_link(authed):
    assert "Help for this page" not in authed.get("/help").text


def test_guide_screenshots_exist_and_are_served(authed):
    html = "".join(part.html for part in guide.load().values())
    images = re.findall(r'<img src="\.\./static/guide/([^"]+)"', html)
    assert images, "the guide should have screenshots"
    assert 'loading="lazy"' in html
    for name in images:
        assert (guide.GUIDE_DIR.parent / "static" / "guide" / name).is_file(), name
        r = authed.get(f"/static/guide/{name}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png", name
