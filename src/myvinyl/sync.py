"""Sync a user's own Discogs collection into myvinyl (Discogs to myvinyl, read only).

Rules (decided in GitHub issue #20):
- Discogs decides what you own (each copy is one album), its folder, and when it was added.
- myvinyl decides conditions, notes, and ratings. A change made on Discogs is applied only
  if you haven't changed that field here since the last sync; if both sides changed, the
  sync records a conflict for you to choose.
- A copy that disappeared from Discogs is listed for you to confirm. Nothing is removed
  automatically.

Only the account that a user's own token belongs to is ever read.
"""

import json
import logging
from collections.abc import Callable
from pathlib import Path

from . import db, discogs

log = logging.getLogger("myvinyl.sync")

# Fields myvinyl owns, compared three ways: as Discogs had them last sync, as you have
# them now, and as Discogs has them now.
OWNED = ("condition", "sleeve_condition", "notes", "rating")
MAX_PAGES = 200  # 100 copies a page: 20,000 copies, the per-user album cap
LABELS = {
    "condition": "Condition",
    "sleeve_condition": "Sleeve condition",
    "notes": "Notes",
    "rating": "Rating",
}


class LimitReached(Exception):
    pass


def _norm(field: str, value):
    """Comparable form of a field value."""
    if field == "rating":
        return float(value) if value not in (None, "") else None
    return (value or "").strip() if isinstance(value, str) or value is None else value


def _blank(value) -> bool:
    return value in (None, "")


def merge(mine: dict, theirs: dict, base: dict | None) -> tuple[dict, list[str]]:
    """Decide each myvinyl-owned field: (changes to apply, fields in conflict).

    `base` is the last-synced Discogs values, or None the first time a copy is linked.
    Discogs having no value (no grade, no notes) never clears anything here.
    """
    changes, conflicts = {}, []
    for field in OWNED:
        m, t = _norm(field, mine.get(field)), _norm(field, theirs.get(field))
        if _blank(t) or t == m:
            continue
        if base is None:  # first link: fill blanks, otherwise ask
            if _blank(m):
                changes[field] = t
            else:
                conflicts.append(field)
            continue
        b = _norm(field, base.get(field))
        if t == b:
            continue  # Discogs unchanged since the last sync; your edit stands
        if m == b:
            changes[field] = t  # only Discogs changed
        else:
            conflicts.append(field)  # both changed
    return changes, conflicts


def apply_copy(
    path: Path,
    owner_id: int,
    run_id: int,
    copy: dict,
    fallback_condition: str,
    queue_light: Callable[[int], None],
    max_albums: int,
) -> str:
    """Bring one Discogs copy into myvinyl. Returns the run counter to bump."""
    album = db.album_by_instance(path, owner_id, copy["instance_id"])
    if album is not None and album["deleted_at"] is not None:
        db.mark_seen(path, album["id"], run_id)  # you trashed it here; don't bring it back
        return "skipped"
    adopted = False
    if album is None:
        album = db.unlinked_album_for_release(path, owner_id, copy["release_id"])
        adopted = album is not None
    snapshot = {field: copy[field] for field in OWNED}
    link = {
        "instance_id": copy["instance_id"],
        "folder_id": copy["folder_id"],
        "added_at": copy["added_at"],
        "run_id": run_id,
        "snapshot": snapshot,
    }

    if album is None:
        if db.count_albums(path, owner_id) >= max_albums:
            raise LimitReached
        data = {
            "artist": copy["artist"],
            "title": copy["title"],
            "year": copy["year"] if copy["year"] and 1900 <= copy["year"] <= 2100 else None,
            "label": copy["label"],
            "format": copy["format"],
            "condition": copy["condition"] or fallback_condition,
            "sleeve_condition": copy["sleeve_condition"],
            "notes": copy["notes"][:2000],
            "value_cents": None,
        }
        album_id = db.create_album(
            path, owner_id, data, release_id=copy["release_id"], rating=copy["rating"]
        )
        db.link_album(path, album_id, changes={}, **link)
        db.log_sync(
            path, owner_id, run_id, album_id, "added", f"{copy['artist']} - {copy['title']}"
        )
        queue_light(album_id)
        return "added"

    base = json.loads(album["discogs_snapshot"]) if album["discogs_snapshot"] else None
    changes, conflicts = merge(dict(album), copy, base)
    if "notes" in changes:
        changes["notes"] = changes["notes"][:2000]
    db.link_album(path, album["id"], changes=changes, **link)
    for field in conflicts:
        db.add_sync_item(path, owner_id, album["id"], "conflict", field, copy[field])
        db.log_sync(path, owner_id, run_id, album["id"], "conflict", LABELS[field])
    if changes:
        detail = ", ".join(LABELS[f] for f in changes)
        db.log_sync(path, owner_id, run_id, album["id"], "updated", detail)
    if adopted:
        db.log_sync(path, owner_id, run_id, album["id"], "linked")
        return "linked"
    return "updated" if changes else "unchanged"


def run_collection_sync(
    path: Path,
    service,
    owner_id: int,
    username: str,
    run_id: int,
    queue_light: Callable[[int], None],
    max_albums: int,
) -> None:
    """Fetch the collection page by page from where the run left off, then finish it.

    Raises Discogs lookup errors to the caller, which records them on the run.
    """
    run = db.get_sync_run(path, run_id)
    fields = service.collection_fields(username)
    db.set_discogs_folders(path, owner_id, service.collection_folders(username))
    page = run["page"]
    while page <= MAX_PAGES:
        pages, items = service.collection_page(username, page)
        db.update_sync_run(path, run_id, pages=min(pages, MAX_PAGES))
        for item in items:
            copy = discogs.parse_instance(item, fields)
            if copy is None:
                db.count_sync(path, run_id, "skipped")  # not vinyl, or malformed
                continue
            try:
                counter = apply_copy(
                    path, owner_id, run_id, copy, run["condition"], queue_light, max_albums
                )
            except LimitReached:
                db.update_sync_run(
                    path, run_id, state="error", message="The collection is at its size limit."
                )
                return
            db.count_sync(path, run_id, counter)
        page += 1
        db.update_sync_run(path, run_id, page=page)  # resume point
        if page > pages:
            break

    # Every page was seen: anything linked that Discogs no longer has is for you to confirm.
    db.clear_sync_items(path, owner_id, "removal")
    missing = db.albums_missing_from_sync(path, owner_id, run_id)
    for album_id in missing:
        db.add_sync_item(path, owner_id, album_id, "removal")
    db.update_sync_run(path, run_id, state="done")
    log.info("Discogs sync %s for user %s done; %d to confirm", run_id, owner_id, len(missing))
