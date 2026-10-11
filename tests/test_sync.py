"""Discogs collection sync: linking, three-way merge, review, resume, and access rules."""

import json
import re
import urllib.error

import pytest
from test_app import (  # noqa: F401 (fixtures are used by name)
    PASSWORD,
    add,
    authed,
    client,
    csrf_from,
    lookup,
)
from test_multiuser import NEW_PASSWORD, album_id_of, second  # noqa: F401
from test_tokens import TOKEN, save_token

from myvinyl import db, discogs, sync

GRADE = {"NM": "Near Mint (NM or M-)", "VG+": "Very Good Plus (VG+)", "VG": "Very Good (VG)"}


def copy(instance, release, *, title="Substance", folder=1, media="", sleeve="", notes="",
         rating=0, fmt="Vinyl", added="2024-05-01T12:00:00-07:00"):  # fmt: skip
    """A raw Discogs collection item, as the API returns it."""
    fields = []
    if media:
        fields.append({"field_id": 1, "value": GRADE.get(media, media)})
    if sleeve:
        fields.append({"field_id": 2, "value": GRADE.get(sleeve, sleeve)})
    if notes:
        fields.append({"field_id": 3, "value": notes})
    return {
        "instance_id": instance,
        "folder_id": folder,
        "rating": rating,
        "date_added": added,
        "notes": fields,
        "basic_information": {
            "id": release,
            "title": title,
            "year": 1987,
            "artists": [{"name": "New Order"}],
            "labels": [{"name": "Factory"}],
            "formats": [{"name": fmt, "qty": "1", "descriptions": ["LP"]}],
        },
    }


def run_sync(client, condition="VG+"):
    token = csrf_from(client, "/import")
    return client.post(
        "/discogs/sync", data={"csrf": token, "condition": condition}, follow_redirects=False
    )


def path_of(tmp_path):
    return tmp_path / "test.db"


def albums(tmp_path, owner=1):
    return {a["discogs_instance_id"]: a for a in db.list_albums(path_of(tmp_path), owner)}


@pytest.fixture
def linked(authed, lookup):
    save_token(authed)  # identity "discogsfan" becomes the linked account
    return authed


# --- Pure logic ------------------------------------------------------------------------------


def test_merge_rules():
    mine = {"condition": "VG+", "sleeve_condition": "", "notes": "mine", "rating": 4.0}
    # First link: blanks are filled, differences become conflicts, Discogs blanks ignored.
    changes, conflicts = sync.merge(
        mine, {"condition": "NM", "sleeve_condition": "VG", "notes": "", "rating": 4}, None
    )
    assert changes == {"sleeve_condition": "VG"} and conflicts == ["condition"]

    base = {"condition": "VG+", "sleeve_condition": "VG", "notes": "mine", "rating": 4}
    # Only Discogs changed: applied. Only mine changed: kept. Both changed: conflict.
    changes, conflicts = sync.merge(
        {**mine, "sleeve_condition": "VG", "rating": 5.0},
        {"condition": "NM", "sleeve_condition": "VG", "notes": "mine", "rating": 3},
        base,
    )
    assert changes == {"condition": "NM"} and conflicts == ["rating"]
    # Discogs unchanged since last sync: your edit stands, nothing to do.
    # You cleared the sleeve grade and edited notes here; Discogs is unchanged: yours stand.
    assert sync.merge({**mine, "notes": "edited"}, base, base) == ({}, [])


def test_parse_instance_skips_bad_items():
    fields = {1: "Media Condition"}
    assert discogs.parse_instance(copy(1, 2, fmt="CD"), fields) is None
    assert discogs.parse_instance({**copy(1, 2), "instance_id": None}, fields) is None
    assert discogs.parse_instance({"basic_information": "x"}, fields) is None
    item = discogs.parse_instance(copy(1, 2, media="Not Graded", added="yesterday"), fields)
    assert item["condition"] == "" and item["added_at"] is None
    item = discogs.parse_instance(copy(1, 2, added="2024-05-01T12:00:00-07:00"), fields)
    assert item["added_at"] == "2024-05-01 19:00:00"


# --- First sync ------------------------------------------------------------------------------


def test_first_sync_imports_private_collection(linked, lookup, tmp_path):
    lookup.discogs_pages = [
        [
            copy(11, 100, media="NM", sleeve="Generic", notes="signed", rating=5),
            copy(12, 100, folder=7, media="VG"),  # a second copy of the same pressing
        ],
        [copy(13, 200, fmt="CD"), copy(14, 300, title="Low-Life")],
    ]
    r = run_sync(linked, condition="G+")
    assert r.status_code == 303 and r.headers["location"] == "/import#discogs"
    assert lookup.synced_username == "discogsfan"

    by_copy = albums(tmp_path)
    assert set(by_copy) == {11, 12, 14}  # the CD was skipped
    a, b, c = by_copy[11], by_copy[12], by_copy[14]
    assert (a["condition"], a["sleeve_condition"], a["notes"], a["rating"]) == (
        "NM",
        "Generic",
        "signed",
        5.0,
    )
    assert b["condition"] == "VG" and b["discogs_folder_id"] == 7
    assert c["condition"] == "G+"  # no grade on Discogs: the condition you chose
    assert a["discogs_release_id"] == 100 and a["pressing_locked"] == 1
    assert a["discogs_added_at"] == "2024-05-01 19:00:00"

    # Light lookups: 2 API calls each, full range left to the hourly refresh.
    assert sorted(lookup.light_calls) == [100, 100, 300] and lookup.calls == []
    assert a["discogs_full_pending"] == 1 and a["discogs_status"] == "done"
    due = db.due_for_refresh(path_of(tmp_path), 7, 50)
    assert set(due) == {a["id"], b["id"], c["id"]}

    run = db.latest_sync_run(path_of(tmp_path), 1)
    assert (run["state"], run["added"], run["skipped"], run["pages"]) == ("done", 3, 1, 2)
    page = linked.get("/import").text
    assert "Linked to Discogs account <strong>discogsfan</strong>" in page
    assert "3 added" in page

    # Folders: filter and chip; CSV has the folder and date.
    grid = linked.get("/?folder=7").text
    assert "Any folder" in grid and f'/albums/{b["id"]}"' in grid
    assert f'/albums/{a["id"]}"' not in grid and "Low-Life" not in grid
    assert "Jazz" in linked.get(f"/albums/{b['id']}").text
    csv_text = linked.get("/export.csv").text
    assert "discogs_folder" in csv_text and "Jazz" in csv_text


def test_full_lookup_clears_light_flag(linked, lookup, tmp_path):
    lookup.discogs_pages = [[copy(11, 100)]]
    run_sync(linked)
    album = albums(tmp_path)[11]
    assert "range across other pressings fills in" in linked.get(f"/albums/{album['id']}").text
    linked.post(f"/albums/{album['id']}/lookup", data={"csrf": csrf_from(linked, "/")})
    assert db.get_album(path_of(tmp_path), album["id"])["discogs_full_pending"] == 0


def test_sync_links_existing_album_and_asks_about_differences(linked, lookup, tmp_path):
    # An album already here, pinned to the same pressing (e.g. from an earlier import).
    lookup.identifier_matches = [
        {"release_id": 100, "artist": "New Order", "title": "Substance", "year": 1987,
         "label": "Factory", "format": "LP", "country": "UK", "catno": "F", "formats": "LP"}
    ]  # fmt: skip
    add(linked, artist="New Order", title="Substance", identifier="12345", condition="VG+")
    lookup.discogs_pages = [[copy(11, 100, media="NM", sleeve="VG", notes="from discogs")]]
    run_sync(linked)

    by_copy = albums(tmp_path)
    assert list(by_copy) == [11]  # linked, not duplicated
    album = by_copy[11]
    assert album["sleeve_condition"] == "VG" and album["notes"] == "from discogs"  # blanks
    assert album["condition"] == "VG+"  # differs: kept until you choose
    items = db.list_sync_items(path_of(tmp_path), 1)
    assert [(i["kind"], i["field"]) for i in items] == [("conflict", "condition")]
    assert db.latest_sync_run(path_of(tmp_path), 1)["linked"] == 1
    assert "1 thing for you to decide" in linked.get("/").text


# --- Resync ----------------------------------------------------------------------------------


def test_resync_applies_conflicts_and_removals(linked, lookup, tmp_path):
    lookup.discogs_pages = [
        [copy(11, 100, media="VG+", notes="a"), copy(12, 200, media="VG+"), copy(13, 300)]
    ]
    run_sync(linked)
    by_copy = albums(tmp_path)
    # You regrade copy 12 here.
    db.set_album_field(path_of(tmp_path), by_copy[12]["id"], "condition", "VG")

    lookup.discogs_pages = [
        [
            copy(11, 100, media="VG+", notes="a, cleaned", folder=7),  # notes changed there
            copy(12, 200, media="NM"),  # changed there too: conflict
        ]  # copy 13 is gone from Discogs
    ]
    run_sync(linked)
    by_copy = albums(tmp_path)
    assert by_copy[11]["notes"] == "a, cleaned" and by_copy[11]["discogs_folder_id"] == 7
    assert by_copy[12]["condition"] == "VG"
    items = {(i["kind"], i["field"]): i for i in db.list_sync_items(path_of(tmp_path), 1)}
    assert set(items) == {("conflict", "condition"), ("removal", "")}
    assert by_copy[13]["deleted_at"] is None  # never removed automatically

    page = linked.get("/discogs/review").text
    assert "Changed in both places (1)" in page and "No longer on Discogs (1)" in page

    token = csrf_from(linked, "/discogs/review")
    conflict, removal = items[("conflict", "condition")], items[("removal", "")]
    linked.post(f"/discogs/review/{conflict['id']}", data={"csrf": token, "action": "theirs"})
    assert db.get_album(path_of(tmp_path), by_copy[12]["id"])["condition"] == "NM"
    linked.post(f"/discogs/review/{removal['id']}", data={"csrf": token, "action": "trash"})
    assert db.get_album(path_of(tmp_path), by_copy[13]["id"]) is None  # in the trash
    assert db.list_sync_items(path_of(tmp_path), 1) == []
    log = linked.get("/discogs/review").text
    assert "Nothing to decide" in log and "trashed" in log and "kept theirs" in log

    # A trashed copy isn't brought back by the next sync.
    lookup.discogs_pages = [[copy(11, 100), copy(12, 200), copy(13, 300)]]
    run_sync(linked)
    assert db.latest_sync_run(path_of(tmp_path), 1)["skipped"] == 1
    assert db.get_album(path_of(tmp_path), by_copy[13]["id"]) is None


def test_keep_removed_copy_and_bulk_choices(linked, lookup, tmp_path):
    lookup.discogs_pages = [[copy(11, 100, media="VG+"), copy(12, 200, media="VG+")]]
    run_sync(linked)
    by_copy = albums(tmp_path)
    for album in by_copy.values():
        db.set_album_field(path_of(tmp_path), album["id"], "condition", "G")
    lookup.discogs_pages = [[copy(11, 100, media="NM")]]
    run_sync(linked)
    token = csrf_from(linked, "/discogs/review")
    removal = next(i for i in db.list_sync_items(path_of(tmp_path), 1) if i["kind"] == "removal")
    linked.post(f"/discogs/review/{removal['id']}", data={"csrf": token, "action": "keep"})
    kept = db.get_album(path_of(tmp_path), by_copy[12]["id"])
    assert kept["deleted_at"] is None and kept["discogs_instance_id"] is None  # unlinked

    linked.post("/discogs/review-all", data={"csrf": token, "action": "mine"})
    assert db.get_album(path_of(tmp_path), by_copy[11]["id"])["condition"] == "G"
    assert db.list_sync_items(path_of(tmp_path), 1) == []
    assert (
        linked.post("/discogs/review-all", data={"csrf": token, "action": "x"}).status_code == 400
    )


def test_sync_resumes_after_an_error(linked, lookup, tmp_path):
    lookup.discogs_pages = [[copy(11, 100)], [copy(12, 200)], [copy(13, 300)]]
    lookup.page_error = {2: urllib.error.URLError("down")}
    run_sync(linked)
    run = db.latest_sync_run(path_of(tmp_path), 1)
    assert run["state"] == "error" and run["page"] == 2 and "could not be reached" in run["message"]
    assert "Resume sync" in linked.get("/import").text
    assert db.list_sync_items(path_of(tmp_path), 1) == []  # no removals from a partial run

    token = csrf_from(linked, "/import")
    linked.post(f"/discogs/sync/{run['id']}/resume", data={"csrf": token})
    run = db.latest_sync_run(path_of(tmp_path), 1)
    assert run["state"] == "done" and set(albums(tmp_path)) == {11, 12, 13}


def test_interrupted_sync_is_marked_on_restart(linked, tmp_path):
    run_id = db.create_sync_run(path_of(tmp_path), 1, "VG+")
    db.init(path_of(tmp_path))  # what a restart does
    assert db.get_sync_run(path_of(tmp_path), run_id)["state"] == "interrupted"


def test_sync_respects_album_cap(linked, lookup, tmp_path, monkeypatch):
    from myvinyl import main

    monkeypatch.setattr(main, "MAX_ALBUMS_PER_USER", 1)
    lookup.discogs_pages = [[copy(11, 100), copy(12, 200)]]
    run_sync(linked)
    run = db.latest_sync_run(path_of(tmp_path), 1)
    assert run["state"] == "error" and "size limit" in run["message"]
    assert len(albums(tmp_path)) == 1


# --- Access rules ----------------------------------------------------------------------------


def test_sync_needs_your_own_linked_account(authed, second, lookup, tmp_path):
    assert run_sync(authed).status_code == 400  # no token yet
    assert "Add your Discogs token" in authed.get("/import").text

    save_token(authed)
    bob = db.get_user_by_name(path_of(tmp_path), "bob")
    authed.post(f"/admin/view/{bob['id']}", data={"csrf": csrf_from(authed, "/admin")})
    assert run_sync(authed).status_code == 400  # never while viewing someone else
    assert "Switch back to your own collection" in authed.get("/import").text

    # Removing the token unlinks the account.
    authed.post("/admin/view-mine", data={"csrf": csrf_from(authed, "/")})
    token = csrf_from(authed, "/account")
    authed.post("/account/discogs-token/delete", data={"csrf": token, "current": PASSWORD})
    assert db.get_user(path_of(tmp_path), 1)["discogs_linked"] is None


def test_review_items_are_private(linked, second, lookup, tmp_path):
    lookup.discogs_pages = [[copy(11, 100)]]
    run_sync(linked)
    lookup.discogs_pages = [[]]
    run_sync(linked)
    item = db.list_sync_items(path_of(tmp_path), 1)[0]
    token = csrf_from(second, "/")
    r = second.post(f"/discogs/review/{item['id']}", data={"csrf": token, "action": "trash"})
    assert r.status_code == 404
    assert "Nothing to decide" in second.get("/discogs/review").text
    assert db.get_album(path_of(tmp_path), item["album_id"]) is not None
    bad = linked.post(
        f"/discogs/review/{item['id']}", data={"csrf": csrf_from(linked, "/"), "action": "zap"}
    )
    assert bad.status_code == 400


def test_sync_condition_must_be_valid(linked):
    r = run_sync(linked, condition="Great")
    assert r.status_code == 422 and "Choose a condition" in r.text


# --- Sleeve grade on the form --------------------------------------------------------------------


def test_sleeve_condition_on_the_form(authed, tmp_path):
    r = add(authed, sleeve_condition="No cover")
    album = db.get_album(path_of(tmp_path), album_id_of(r.headers["location"]))
    assert album["sleeve_condition"] == "No cover"
    assert "sleeve No cover" in authed.get(r.headers["location"]).text
    r = add(authed, sleeve_condition="Shiny")
    assert r.status_code == 422 and "Choose a sleeve condition" in r.text
    edit = authed.get(f"/albums/{album['id']}/edit").text
    assert re.search(r'<option value="No cover" selected>', edit)


def test_snapshot_records_discogs_values(linked, lookup, tmp_path):
    lookup.discogs_pages = [[copy(11, 100, media="NM", notes="x", rating=3)]]
    run_sync(linked)
    snap = json.loads(albums(tmp_path)[11]["discogs_snapshot"])
    assert snap == {"condition": "NM", "notes": "x", "rating": 3.0, "sleeve_condition": ""}
    assert TOKEN not in json.dumps(snap)
