"""Accounts, access control, trash, purchases, notes, wishlist, views, covers, backups."""

import re

import pytest
from fastapi.testclient import TestClient
from test_app import (  # noqa: F401 (fixtures are used by name)
    PASSWORD,
    add,
    authed,
    client,
    csrf_from,
    first_album_link,
    login,
    lookup,
)

from myvinyl import db
from myvinyl.storage import Storage

NEW_PASSWORD = "another long passphrase"


def album_id_of(link):
    return int(link.rsplit("/", 1)[1])


def make_link(admin, role="member"):
    token = csrf_from(admin, "/admin")
    page = admin.post("/admin/invite", data={"csrf": token, "role": role}).text
    return re.search(r'value="https://testserver(/link/[^"]+)"', page).group(1)


@pytest.fixture
def second(authed, tmp_path):
    """A second user ('bob') with their own browser session."""
    link = make_link(authed)
    other = TestClient(authed.app, base_url="https://testserver")
    token = csrf_from(other, link)
    r = other.post(
        link,
        data={"csrf": token, "username": "bob", "password": NEW_PASSWORD, "confirm": NEW_PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 303
    return other


# --- Accounts and invites ------------------------------------------------------------------


def test_invite_creates_member_with_private_collection(authed, second, tmp_path):
    add(authed, title="Admin Album")
    add(second, title="Bob Album")
    assert "Bob Album" in second.get("/").text and "Admin Album" not in second.get("/").text
    assert "Admin Album" in authed.get("/").text and "Bob Album" not in authed.get("/").text

    # Bob can't open, edit, or delete the admin's album: 404, not 403.
    admin_album = first_album_link(authed)
    token = csrf_from(second, "/")
    assert second.get(admin_album).status_code == 404
    assert second.get(admin_album + "/edit").status_code == 404
    assert second.post(admin_album + "/delete", data={"csrf": token}).status_code == 404
    assert second.post(admin_album + "/notes", data={"csrf": token, "body": "x"}).status_code == 404
    assert second.get("/admin").status_code == 404


def test_admin_can_view_and_change_any_collection(authed, second, tmp_path):
    add(second, title="Bob Album")
    bob = db.get_user_by_name(tmp_path / "test.db", "bob")
    token = csrf_from(authed, "/admin")
    authed.post(f"/admin/view/{bob['id']}", data={"csrf": token})
    page = authed.get("/").text
    assert "Viewing <strong>bob</strong>" in page and "Bob Album" in page
    # Adding while viewing puts the album in bob's collection.
    add(authed, title="Gift From Admin")
    assert "Gift From Admin" in second.get("/").text
    link = first_album_link(authed)
    assert authed.get(link).status_code == 200
    authed.post("/admin/view-mine", data={"csrf": csrf_from(authed, "/")})
    assert "Bob Album" not in authed.get("/").text


def test_invite_link_is_single_use_and_validated(authed, tmp_path):
    link = make_link(authed)
    other = TestClient(authed.app, base_url="https://testserver")
    token = csrf_from(other, link)

    def submit(**data):
        return other.post(link, data={"csrf": token, **data}, follow_redirects=False)

    r = submit(username="x", password=NEW_PASSWORD, confirm=NEW_PASSWORD)
    assert r.status_code == 422 and "3 to 32" in r.text
    r = submit(username="admin", password=NEW_PASSWORD, confirm=NEW_PASSWORD)
    assert "That username is taken" in r.text
    r = submit(username="carol", password="short", confirm="short")
    assert "at least 12" in r.text
    r = submit(username="carol", password=NEW_PASSWORD, confirm=NEW_PASSWORD + "x")
    assert "do not match" in r.text
    assert submit(username="carol", password=NEW_PASSWORD, confirm=NEW_PASSWORD).status_code == 303
    # Used: now invalid for anyone.
    assert other.get(link).status_code == 404
    assert "Link not valid" in other.get(link).text


def test_expired_and_forged_links_rejected(authed, tmp_path):
    link = make_link(authed)
    with db.connect(tmp_path / "test.db") as conn:
        conn.execute("UPDATE invites SET expires_at = datetime('now', '-1 minute')")
    assert authed.get(link).status_code == 404
    assert authed.get("/link/not-a-real-token").status_code == 404


def test_tokens_are_stored_hashed(authed, tmp_path):
    link = make_link(authed)
    token = link.rsplit("/", 1)[1]
    with db.connect(tmp_path / "test.db") as conn:
        stored = conn.execute("SELECT token_hash FROM invites").fetchone()[0]
    assert token not in stored and len(stored) == 64


def test_password_reset_signs_out_old_sessions(authed, second, tmp_path):
    bob = db.get_user_by_name(tmp_path / "test.db", "bob")
    token = csrf_from(authed, "/admin")
    page = authed.post(f"/admin/users/{bob['id']}/reset", data={"csrf": token}).text
    link = re.search(r'value="https://testserver(/link/[^"]+)"', page).group(1)

    fresh = TestClient(authed.app, base_url="https://testserver")
    t = csrf_from(fresh, link)
    fresh.post(
        link,
        data={"csrf": t, "password": "brand new passphrase", "confirm": "brand new passphrase"},
    )
    # Bob's old session is no longer valid; the new password works.
    assert second.get("/", follow_redirects=False).status_code == 303
    assert login(fresh, "brand new passphrase", "bob").status_code in (303, 200)


def test_disable_user_kicks_session_and_blocks_login(authed, second, tmp_path):
    bob = db.get_user_by_name(tmp_path / "test.db", "bob")
    token = csrf_from(authed, "/admin")
    authed.post(f"/admin/users/{bob['id']}/disable", data={"csrf": token})
    assert second.get("/", follow_redirects=False).status_code == 303
    r = login(second, NEW_PASSWORD, "bob")
    assert r.status_code == 401 and "Incorrect username or password" in r.text


def test_last_admin_is_protected(authed, tmp_path):
    me = db.get_user_by_name(tmp_path / "test.db", "admin")
    token = csrf_from(authed, "/admin")
    r = authed.post(f"/admin/users/{me['id']}/role", data={"csrf": token, "role": "member"})
    assert r.status_code == 400
    r = authed.post(f"/admin/users/{me['id']}/disable", data={"csrf": token})
    assert r.status_code == 400


def test_login_does_not_reveal_usernames(client):
    a = login(client, "wrong", "admin")
    b = login(client, "wrong", "nobody")
    assert a.status_code == b.status_code == 401
    assert "Incorrect username or password" in a.text and "Incorrect username or password" in b.text


def test_change_password_and_profile(authed, tmp_path):
    token = csrf_from(authed, "/account")
    r = authed.post(
        "/account",
        data={"csrf": token, "current": "nope", "password": NEW_PASSWORD, "confirm": NEW_PASSWORD},
    )
    assert "your current password" in r.text
    r = authed.post(
        "/account",
        data={
            "csrf": token,
            "current": PASSWORD,
            "password": NEW_PASSWORD,
            "confirm": NEW_PASSWORD,
        },
    )
    assert "Password changed" in r.text
    token = csrf_from(authed, "/account")
    r = authed.post(
        "/account/profile",
        data={
            "csrf": token,
            "display_name": "John <b>",
            "bio": "Jazz",
            "theme": "light",
            "discogs_username": "vinylfan",
        },
    )
    assert 'data-theme="light"' in r.text and "John &lt;b&gt;" in r.text
    assert 'value="vinylfan"' in authed.get("/import").text
    for theme in ("gray", "navy", "royal", "auto"):
        authed.post("/account/profile", data={"csrf": token, "theme": theme})
        assert f'data-theme="{theme}"' in authed.get("/").text
    r = authed.post("/account/profile", data={"csrf": token, "theme": "evil"})
    assert r.status_code == 422


def test_healthz_is_public(client):
    assert client.get("/healthz").text == "ok"


# --- Trash (soft delete) -------------------------------------------------------------------


def test_delete_goes_to_trash_and_restores(authed, tmp_path):
    add(authed, title="Doomed")
    link = first_album_link(authed)
    token = csrf_from(authed, link)
    authed.post(link + "/delete", data={"csrf": token})
    assert "Doomed" not in authed.get("/").text
    assert authed.get(link).status_code == 404
    assert "Doomed" in authed.get("/trash").text

    album_id = album_id_of(link)
    authed.post(f"/trash/{album_id}/restore", data={"csrf": token})
    assert "Doomed" in authed.get("/").text

    authed.post(link + "/delete", data={"csrf": token})
    authed.post(f"/trash/{album_id}/purge", data={"csrf": token})
    assert db.get_album(tmp_path / "test.db", album_id, include_deleted=True) is None


def test_purge_requires_trash_first_and_empty_trash(authed, tmp_path):
    add(authed, title="One")
    add(authed, title="Two")
    links = re.findall(r'href="(/albums/\d+)"', authed.get("/").text)
    token = csrf_from(authed, "/")
    first = album_id_of(links[0])
    assert authed.post(f"/trash/{first}/purge", data={"csrf": token}).status_code == 400
    for link in set(links):
        authed.post(link + "/delete", data={"csrf": token})
    authed.post("/trash/empty", data={"csrf": token})
    assert "The trash is empty" in authed.get("/trash").text


def test_old_trash_is_purged_hourly(authed, tmp_path):
    add(authed, title="Old")
    link = first_album_link(authed)
    authed.post(link + "/delete", data={"csrf": csrf_from(authed, link)})
    with db.connect(tmp_path / "test.db") as conn:
        conn.execute("UPDATE albums SET deleted_at = datetime('now', '-31 days')")
    assert "purged 1" in authed.app.state.hourly()


# --- Purchases and notes ---------------------------------------------------------------------


def test_purchase_price_and_gain(authed):
    r = add(
        authed,
        purchase_price="10.00",
        purchase_date="2024-05-01",
        purchased_from="Record Store Day",
    )
    page = authed.get(r.headers["location"]).text
    assert "$10.00" in page and "+$14.48" in page and "Record Store Day" in page
    html = authed.get("/").text
    assert "+$14.48" in html  # collection gain
    r = add(authed, purchase_date="2999-01-01")
    assert r.status_code == 422 and "between 1900 and today" in r.text


def test_dated_notes(authed):
    add(authed)
    link = first_album_link(authed)
    token = csrf_from(authed, link)
    authed.post(link + "/notes", data={"csrf": token, "body": "Cleaned <with> the VPI"})
    page = authed.get(link).text
    assert "Cleaned &lt;with&gt; the VPI" in page and re.search(r"[AP]M E[SD]T", page)
    note_id = re.search(r"/notes/(\d+)/delete", page).group(1)
    assert authed.post(link + "/notes", data={"csrf": token, "body": ""}).status_code == 400
    authed.post(f"{link}/notes/{note_id}/delete", data={"csrf": token})
    assert "Cleaned" not in authed.get(link).text


# --- Wishlist ------------------------------------------------------------------------------


def test_wishlist_price_check_and_got_it(authed, lookup, tmp_path):
    token = csrf_from(authed, "/wishlist")
    authed.post(
        "/wishlist", data={"csrf": token, "artist": "Can", "title": "Tago Mago", "target": "30"}
    )
    page = authed.get("/wishlist").text
    assert "Tago Mago" in page and "$25.00" in page and "at or below target" in page

    wish_id = re.search(r"/wishlist/(\d+)/got", page).group(1)
    r = authed.post(
        f"/wishlist/{wish_id}/got",
        data={"csrf": token, "condition": "NM", "paid": "22.50"},
        follow_redirects=False,
    )
    album = db.get_album(tmp_path / "test.db", album_id_of(r.headers["location"]))
    assert album["title"] == "Tago Mago" and album["purchase_cents"] == 2250
    assert "Tago Mago" not in authed.get("/wishlist").text


def test_wishlist_validation_and_wantlist_import(authed, lookup):
    token = csrf_from(authed, "/wishlist")
    r = authed.post("/wishlist", data={"csrf": token, "artist": "", "title": "X"})
    assert r.status_code == 422
    lookup.want_items = [
        {
            "release_id": 9,
            "artist": "Neu!",
            "title": "Neu!",
            "year": 1972,
            "label": "Brain",
            "format": "LP",
            "notes": "first press",
        },
    ]
    authed.post("/wishlist/import", data={"csrf": token, "username": "vinylfan"})
    page = authed.get("/wishlist").text
    assert "Imported 1" in page and "first press" in page
    authed.post("/wishlist/import", data={"csrf": token, "username": "vinylfan"})
    assert "1 were already" in authed.get("/wishlist").text


def test_other_users_cannot_touch_my_wishes(authed, second):
    token = csrf_from(authed, "/wishlist")
    authed.post("/wishlist", data={"csrf": token, "artist": "Can", "title": "Ege Bamyasi"})
    wish_id = re.search(r"/wishlist/(\d+)/got", authed.get("/wishlist").text).group(1)
    t2 = csrf_from(second, "/wishlist")
    assert second.post(f"/wishlist/{wish_id}/delete", data={"csrf": t2}).status_code == 404
    assert "Ege Bamyasi" not in second.get("/wishlist").text


# --- Filters, grid, stats, report, CSV -------------------------------------------------------


def test_filters_grid_stats_and_report(authed):
    add(authed, artist="New Order", title="Substance", year="1987", format="2xLP")
    add(authed, artist="Miles Davis", title="Kind of Blue", year="1959")
    html = authed.get("/?genre=Electronic&decade=1980").text
    assert "Substance" in html and "Kind of Blue" not in html and "(filtered)" in html
    html = authed.get("/?format=LP").text
    assert "Kind of Blue" in html and "Substance" not in html
    assert "Kind of Blue" in authed.get("/?min_value=1&max_value=100").text
    assert "No albums match" in authed.get("/?min_rating=5").text
    # Bad filter values are ignored, not errors.
    assert authed.get("/?decade=abc&format=<x>&min_value=lots").status_code == 200

    grid = authed.get("/?view=grid").text
    assert 'class="cover-grid"' in grid and re.search(r'src="/covers/\d+"', grid)

    stats = authed.get("/stats?decade=1980").text
    assert "Genre" in stats and "Electronic" in stats and "<rect" in stats

    report = authed.get("/report?decade=1980").text
    assert "Print" in report and "decade = 1980" in report and "Substance" in report
    assert "Kind of Blue" not in report

    csv = authed.get("/export.csv?decade=1980").text
    assert "Substance" in csv and "Kind of Blue" not in csv and "Electronic" in csv


# --- Covers and backups --------------------------------------------------------------------


def test_cover_failures_and_access(authed, second, lookup):
    lookup.image_error = True
    add(second, title="No Art")
    link = first_album_link(second)
    assert second.get(f"/covers/{album_id_of(link)}").status_code == 404
    lookup.image_error = False
    add(authed, title="Has Art")
    mine = first_album_link(authed)
    # Bob can't read the admin's cover file either.
    assert authed.get(f"/covers/{album_id_of(mine)}").status_code == 200
    assert second.get(f"/covers/{album_id_of(mine)}").status_code == 404


def test_cover_path_refuses_traversal(tmp_path):
    storage = Storage(tmp_path / "x.db")
    for bad in ("../x.db", "1.exe", "a.png", "1.png/../../x", ""):
        assert storage.cover_path(bad) is None


def test_backups_and_retention(authed, tmp_path):
    token = csrf_from(authed, "/admin")
    authed.post("/admin/backup", data={"csrf": token})
    assert "myvinyl-" in authed.get("/admin").text
    storage = Storage(tmp_path / "test.db")
    import time

    for _ in range(3):
        time.sleep(1.1)  # names have one-second resolution
        storage.backup(keep=2)
    assert len(storage.list_backups()) == 2


# --- Usernames and emails ------------------------------------------------------------------


def test_user_changes_own_username_and_email(authed, tmp_path):
    token = csrf_from(authed, "/account")

    def save(**data):
        return authed.post("/account/identity", data={"csrf": token, **data})

    r = save(username="john", email="john@example.com", current="wrong")
    assert r.status_code == 422 and "your current password" in r.text
    r = save(username="x", email="", current=PASSWORD)
    assert "3 to 32" in r.text
    r = save(username="john", email="not-an-email", current=PASSWORD)
    assert "valid email" in r.text
    r = save(username="john", email="John@Example.com", current=PASSWORD)
    assert r.status_code == 200 and "Saved." in r.text
    user = db.get_user(tmp_path / "test.db", 1)
    assert user["username"] == "john" and user["email"] == "John@Example.com"
    # Still signed in (sessions are tied to the account, not the name); new name logs in.
    assert authed.get("/").status_code == 200
    fresh = TestClient(authed.app, base_url="https://testserver")
    assert login(fresh, PASSWORD, "admin").status_code == 401
    assert login(fresh, PASSWORD, "john").status_code == 303
    # Clearing the email is allowed.
    save(username="john", email="", current=PASSWORD)
    assert db.get_user(tmp_path / "test.db", 1)["email"] is None


def test_usernames_and_emails_stay_unique(authed, second, tmp_path):
    token = csrf_from(authed, "/account")
    authed.post(
        "/account/identity",
        data={"csrf": token, "username": "admin", "email": "me@example.com", "current": PASSWORD},
    )
    t2 = csrf_from(second, "/account")
    r = second.post(
        "/account/identity",
        data={"csrf": t2, "username": "ADMIN", "email": "", "current": NEW_PASSWORD},
    )
    assert "username is already used" in r.text
    r = second.post(
        "/account/identity",
        data={"csrf": t2, "username": "bob", "email": "ME@example.com", "current": NEW_PASSWORD},
    )
    assert "email is already used" in r.text


def test_admin_sets_username_and_email(authed, second, tmp_path):
    bob = db.get_user_by_name(tmp_path / "test.db", "bob")
    token = csrf_from(authed, "/admin")
    r = authed.post(
        f"/admin/users/{bob['id']}/identity",
        data={"csrf": token, "username": "robert", "email": "rob@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    user = db.get_user(tmp_path / "test.db", bob["id"])
    assert user["username"] == "robert" and user["email"] == "rob@example.com"
    assert 'value="rob@example.com"' in authed.get("/admin").text
    r = authed.post(
        f"/admin/users/{bob['id']}/identity",
        data={"csrf": token, "username": "admin", "email": ""},
    )
    assert r.status_code == 422 and "username is already used" in r.text
    # Members can't use the admin endpoint.
    t2 = csrf_from(second, "/account")
    r = second.post(
        f"/admin/users/{bob['id']}/identity", data={"csrf": t2, "username": "z12", "email": ""}
    )
    assert r.status_code == 404


def test_invite_accepts_optional_email(authed, tmp_path):
    link = make_link(authed)
    other = TestClient(authed.app, base_url="https://testserver")
    token = csrf_from(other, link)
    base = {"csrf": token, "username": "carol", "password": NEW_PASSWORD, "confirm": NEW_PASSWORD}
    r = other.post(link, data={**base, "email": "bad"}, follow_redirects=False)
    assert r.status_code == 422 and "valid email" in r.text
    r = other.post(link, data={**base, "email": "carol@example.com"}, follow_redirects=False)
    assert r.status_code == 303
    assert db.get_user_by_name(tmp_path / "test.db", "carol")["email"] == "carol@example.com"
