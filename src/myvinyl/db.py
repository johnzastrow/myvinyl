"""SQLite storage. All queries are parameterized."""

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
    value_source TEXT,          -- 'manual' or 'discogs'
    discogs_release_id INTEGER,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

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
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Columns added after 0.1.0; existing databases are upgraded in place.
MIGRATIONS = {
    "value_source": "ALTER TABLE albums ADD COLUMN value_source TEXT",
    "discogs_release_id": "ALTER TABLE albums ADD COLUMN discogs_release_id INTEGER",
}


def init(path: Path) -> None:
    with connect(path) as conn:
        conn.execute(SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(albums)")}
        for column, ddl in MIGRATIONS.items():
            if column not in existing:
                conn.execute(ddl)


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


def totals(path: Path) -> tuple[int, int]:
    with connect(path) as conn:
        row = conn.execute("SELECT COUNT(*), COALESCE(SUM(value_cents), 0) FROM albums").fetchone()
    return row[0], row[1]


def get_album(path: Path, album_id: int):
    with connect(path) as conn:
        return conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()


def create_album(path: Path, data: dict) -> int:
    with connect(path) as conn:
        cur = conn.execute(
            "INSERT INTO albums (artist, title, year, label, format, condition, notes, value_cents)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [data[c] for c in COLUMNS],
        )
        return cur.lastrowid


def update_album(path: Path, album_id: int, data: dict) -> bool:
    with connect(path) as conn:
        cur = conn.execute(
            "UPDATE albums SET artist = ?, title = ?, year = ?, label = ?, format = ?,"
            " condition = ?, notes = ?, value_cents = ?, updated_at = CURRENT_TIMESTAMP"
            " WHERE id = ?",
            [*(data[c] for c in COLUMNS), album_id],
        )
        return cur.rowcount == 1


def set_discogs_value(path: Path, album_id: int, release_id: int, value_cents: int | None) -> None:
    with connect(path) as conn:
        conn.execute(
            "UPDATE albums SET discogs_release_id = ?, value_cents = ?, value_source = 'discogs',"
            " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (release_id, value_cents, album_id),
        )


def mark_value_manual(path: Path, album_id: int) -> None:
    with connect(path) as conn:
        conn.execute("UPDATE albums SET value_source = 'manual' WHERE id = ?", (album_id,))


def delete_album(path: Path, album_id: int) -> bool:
    with connect(path) as conn:
        return conn.execute("DELETE FROM albums WHERE id = ?", (album_id,)).rowcount == 1
