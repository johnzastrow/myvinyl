"""SQLite storage. All queries are parameterized."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .albums import CONDITIONS

SCHEMA = """
CREATE TABLE IF NOT EXISTS albums (
    id          INTEGER PRIMARY KEY,
    artist      TEXT NOT NULL,
    title       TEXT NOT NULL,
    year        INTEGER,
    label       TEXT NOT NULL DEFAULT '',
    format      TEXT NOT NULL,
    condition   TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT '',
    value_cents INTEGER,
    value_source TEXT,          -- 'manual', 'discogs', or NULL (no value yet)
    discogs_release_id INTEGER, -- pressing chosen as the best match
    value_low_cents INTEGER,    -- range of lowest asking prices across matching pressings
    value_high_cents INTEGER,
    value_median_cents INTEGER,
    discogs_status TEXT,        -- 'pending', 'done', 'none' (no match), 'error'
    discogs_checked_at TEXT,
    cover_uri TEXT,             -- album art you picked from the Discogs images
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Full Discogs release record for the chosen pressing, kept as raw JSON.
CREATE TABLE IF NOT EXISTS discogs_releases (
    album_id   INTEGER PRIMARY KEY REFERENCES albums(id) ON DELETE CASCADE,
    release_id INTEGER NOT NULL,
    data       TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Every pressing checked for the value range, with its marketplace snapshot.
CREATE TABLE IF NOT EXISTS discogs_pressings (
    album_id     INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    release_id   INTEGER NOT NULL,
    rank         INTEGER NOT NULL,
    title        TEXT NOT NULL,
    year         INTEGER,
    country      TEXT NOT NULL,
    label        TEXT NOT NULL,
    catno        TEXT NOT NULL,
    formats      TEXT NOT NULL,
    price_cents  INTEGER,
    num_for_sale INTEGER,
    PRIMARY KEY (album_id, release_id)
);
"""

# Columns added to `albums` after 0.1.0; existing databases are upgraded in place.
MIGRATIONS = {
    "value_source": "ALTER TABLE albums ADD COLUMN value_source TEXT",
    "discogs_release_id": "ALTER TABLE albums ADD COLUMN discogs_release_id INTEGER",
    "value_low_cents": "ALTER TABLE albums ADD COLUMN value_low_cents INTEGER",
    "value_high_cents": "ALTER TABLE albums ADD COLUMN value_high_cents INTEGER",
    "value_median_cents": "ALTER TABLE albums ADD COLUMN value_median_cents INTEGER",
    "discogs_status": "ALTER TABLE albums ADD COLUMN discogs_status TEXT",
    "discogs_checked_at": "ALTER TABLE albums ADD COLUMN discogs_checked_at TEXT",
    "cover_uri": "ALTER TABLE albums ADD COLUMN cover_uri TEXT",
}

_condition_rank = " ".join(f"WHEN '{c}' THEN {i}" for i, c in enumerate(CONDITIONS))

# Allowlist of sortable columns -> SQL expressions. Only these fixed strings are ever
# interpolated into ORDER BY; user input selects a key and is never inserted directly.
SORTS = {
    "artist": "artist COLLATE NOCASE",
    "title": "title COLLATE NOCASE",
    "year": "year",
    "label": "label COLLATE NOCASE",
    "format": "format",
    "condition": f"CASE condition {_condition_rank} END",
    "value": "value_cents",
}

COLUMNS = ("artist", "title", "year", "label", "format", "condition", "notes", "value_cents")


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init(path: Path) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(albums)")}
        for column, ddl in MIGRATIONS.items():
            if column not in existing:
                conn.execute(ddl)
        # Lookups run in the background; any left 'pending' were cut off by a restart.
        conn.execute("UPDATE albums SET discogs_status = 'error' WHERE discogs_status = 'pending'")


def list_albums(path: Path, q: str = "", sort: str = "artist", direction: str = "asc"):
    order = SORTS.get(sort, SORTS["artist"])
    dir_sql = "DESC" if direction == "desc" else "ASC"
    params: list[str] = []
    where = ""
    if q:
        # Escape LIKE wildcards so the search is literal.
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where = (
            "WHERE artist LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' "
            "OR label LIKE ? ESCAPE '\\'"
        )
        params = [pattern] * 3
    sql = (
        f"SELECT * FROM albums {where} ORDER BY {order} {dir_sql}, "  # noqa: S608 (allowlisted)
        "artist COLLATE NOCASE, title COLLATE NOCASE"
    )
    with connect(path) as conn:
        return conn.execute(sql, params).fetchall()


def totals(path: Path) -> dict[str, int]:
    """Album count, total value, and the collection's total low/high range."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(value_cents), 0) AS value,"
            " COALESCE(SUM(value_low_cents), 0) AS low,"
            " COALESCE(SUM(value_high_cents), 0) AS high FROM albums"
        ).fetchone()
    return dict(row)


def get_album(path: Path, album_id: int):
    with connect(path) as conn:
        return conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()


def create_album(path: Path, data: dict) -> int:
    source = None if data["value_cents"] is None else "manual"
    with connect(path) as conn:
        cur = conn.execute(
            "INSERT INTO albums (artist, title, year, label, format, condition, notes,"
            " value_cents, value_source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [*(data[c] for c in COLUMNS), source],
        )
        return cur.lastrowid


def update_album(path: Path, album_id: int, data: dict, value_source: str | None) -> bool:
    with connect(path) as conn:
        cur = conn.execute(
            "UPDATE albums SET artist = ?, title = ?, year = ?, label = ?, format = ?,"
            " condition = ?, notes = ?, value_cents = ?, value_source = ?,"
            " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [*(data[c] for c in COLUMNS), value_source, album_id],
        )
        return cur.rowcount == 1


def set_cover(path: Path, album_id: int, uri: str) -> None:
    with connect(path) as conn:
        conn.execute("UPDATE albums SET cover_uri = ? WHERE id = ?", (uri, album_id))


def delete_album(path: Path, album_id: int) -> bool:
    with connect(path) as conn:
        return conn.execute("DELETE FROM albums WHERE id = ?", (album_id,)).rowcount == 1


# --- Discogs data --------------------------------------------------------------------------


def set_discogs_status(path: Path, album_id: int, status: str) -> None:
    with connect(path) as conn:
        conn.execute(
            "UPDATE albums SET discogs_status = ?, discogs_checked_at = CURRENT_TIMESTAMP"
            " WHERE id = ?",
            (status, album_id),
        )


def save_enrichment(path: Path, album_id: int, e) -> None:
    """Store a Discogs lookup atomically.

    The value is only set when you have not entered one yourself, and label/year are only
    filled when blank: anything you typed is never overwritten.
    """
    release = e.release if isinstance(e.release, dict) else {}
    labels = release.get("labels") if isinstance(release.get("labels"), list) else []
    label = next(
        (
            lbl["name"][:200]
            for lbl in labels
            if isinstance(lbl, dict) and isinstance(lbl.get("name"), str)
        ),
        "",
    )
    year = release.get("year") if isinstance(release.get("year"), int) else None
    year = year if year and 1900 <= year <= 2100 else None

    with connect(path) as conn:
        conn.execute(
            "UPDATE albums SET"
            " discogs_release_id = ?, value_low_cents = ?, value_high_cents = ?,"
            " value_median_cents = ?, discogs_status = 'done',"
            " discogs_checked_at = CURRENT_TIMESTAMP,"
            " value_cents = CASE WHEN value_source = 'manual' THEN value_cents ELSE ? END,"
            " value_source = CASE WHEN value_source = 'manual' THEN 'manual'"
            "                     WHEN ? IS NULL THEN NULL ELSE 'discogs' END,"
            " label = CASE WHEN label = '' THEN ? ELSE label END,"
            " year = COALESCE(year, ?)"
            " WHERE id = ?",
            (
                e.release_id,
                e.low_cents,
                e.high_cents,
                e.median_cents,
                e.value_cents,
                e.value_cents,
                label,
                year,
                album_id,
            ),
        )
        conn.execute("DELETE FROM discogs_pressings WHERE album_id = ?", (album_id,))
        conn.executemany(
            "INSERT INTO discogs_pressings (album_id, release_id, rank, title, year, country,"
            " label, catno, formats, price_cents, num_for_sale)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    album_id,
                    p.release_id,
                    rank,
                    p.title,
                    p.year,
                    p.country,
                    p.label,
                    p.catno,
                    p.formats,
                    p.price_cents,
                    p.num_for_sale,
                )
                for rank, p in enumerate(e.pressings)
            ],
        )
        conn.execute(
            "INSERT OR REPLACE INTO discogs_releases (album_id, release_id, data) VALUES (?, ?, ?)",
            (album_id, e.release_id, json.dumps(release)),
        )


def get_discogs(path: Path, album_id: int) -> tuple[dict | None, list]:
    """Return (release record or None, pressings in rank order)."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT data FROM discogs_releases WHERE album_id = ?", (album_id,)
        ).fetchone()
        pressings = conn.execute(
            "SELECT * FROM discogs_pressings WHERE album_id = ? ORDER BY rank", (album_id,)
        ).fetchall()
    return (json.loads(row["data"]) if row else None), pressings
