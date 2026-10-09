"""SQLite storage. All queries are parameterized.

Every collection query is scoped to an owner (a user id). Albums are soft-deleted: a
deleted album keeps its data in the trash until it is restored or purged.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .albums import CONDITIONS

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'member',   -- 'admin' or 'member'
    disabled        INTEGER NOT NULL DEFAULT 0,
    session_version INTEGER NOT NULL DEFAULT 1,       -- bumped to sign out everywhere
    display_name    TEXT NOT NULL DEFAULT '',
    bio             TEXT NOT NULL DEFAULT '',
    theme           TEXT NOT NULL DEFAULT 'auto',
    discogs_username TEXT NOT NULL DEFAULT '',
    email           TEXT,             -- optional; for future email features (SMTP2GO)
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at   TEXT
);

-- One-time links: account invites and password resets. Only a hash of the token is stored.
CREATE TABLE IF NOT EXISTS invites (
    id          INTEGER PRIMARY KEY,
    token_hash  TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,                 -- 'invite' or 'reset'
    role        TEXT NOT NULL DEFAULT 'member',
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,  -- reset target
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at  TEXT NOT NULL,
    used_at     TEXT
);

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
    pressing_locked INTEGER NOT NULL DEFAULT 0,  -- 1 = you picked the pressing
    identifier TEXT NOT NULL DEFAULT '',         -- barcode or catalog # you entered
    rating REAL,                -- your rating, 0.5 to 5 in half steps
    review TEXT NOT NULL DEFAULT '',
    owner_id INTEGER REFERENCES users(id),
    deleted_at TEXT,            -- set when moved to the trash
    purchase_cents INTEGER,
    purchase_date TEXT,         -- YYYY-MM-DD
    purchased_from TEXT NOT NULL DEFAULT '',
    genres TEXT NOT NULL DEFAULT '',   -- '|'-separated, from Discogs
    styles TEXT NOT NULL DEFAULT '',
    cover_file TEXT,            -- locally saved copy of the displayed cover
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

-- One row per completed Discogs lookup, for the value-history chart.
CREATE TABLE IF NOT EXISTS value_history (
    id           INTEGER PRIMARY KEY,
    album_id     INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    checked_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    release_id   INTEGER,
    value_cents  INTEGER,
    low_cents    INTEGER,
    median_cents INTEGER,
    high_cents   INTEGER
);
CREATE INDEX IF NOT EXISTS ix_value_history ON value_history (album_id, checked_at);

-- Your rating and note for each track (positions come from the Discogs tracklist).
CREATE TABLE IF NOT EXISTS track_ratings (
    album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    position TEXT NOT NULL,
    title    TEXT NOT NULL,
    rating   REAL,
    note     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (album_id, position)
);

-- Dated personal notes: an ad hoc journal per album.
CREATE TABLE IF NOT EXISTS album_notes (
    id         INTEGER PRIMARY KEY,
    album_id   INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    author_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    body       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_album_notes ON album_notes (album_id, created_at);

CREATE TABLE IF NOT EXISTS wishlist (
    id                 INTEGER PRIMARY KEY,
    owner_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    artist             TEXT NOT NULL,
    title              TEXT NOT NULL,
    year               INTEGER,
    notes              TEXT NOT NULL DEFAULT '',
    target_cents       INTEGER,
    discogs_release_id INTEGER,
    pressing_locked    INTEGER NOT NULL DEFAULT 0,
    low_cents          INTEGER,       -- lowest current asking price
    num_for_sale       INTEGER,
    discogs_status     TEXT,
    checked_at         TEXT,
    created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_wishlist_owner ON wishlist (owner_id);
"""

# Columns added to `albums` after 0.1.0; existing databases are upgraded in place.
MIGRATIONS = {
    "value_source": "TEXT",
    "discogs_release_id": "INTEGER",
    "value_low_cents": "INTEGER",
    "value_high_cents": "INTEGER",
    "value_median_cents": "INTEGER",
    "discogs_status": "TEXT",
    "discogs_checked_at": "TEXT",
    "cover_uri": "TEXT",
    "pressing_locked": "INTEGER NOT NULL DEFAULT 0",
    "identifier": "TEXT NOT NULL DEFAULT ''",
    "rating": "REAL",
    "review": "TEXT NOT NULL DEFAULT ''",
    "owner_id": "INTEGER REFERENCES users(id)",
    "deleted_at": "TEXT",
    "purchase_cents": "INTEGER",
    "purchase_date": "TEXT",
    "purchased_from": "TEXT NOT NULL DEFAULT ''",
    "genres": "TEXT NOT NULL DEFAULT ''",
    "styles": "TEXT NOT NULL DEFAULT ''",
    "cover_file": "TEXT",
}

# Columns added to `users` after it was introduced.
USER_MIGRATIONS = {
    "display_name": "TEXT NOT NULL DEFAULT ''",
    "bio": "TEXT NOT NULL DEFAULT ''",
    "theme": "TEXT NOT NULL DEFAULT 'auto'",
    "discogs_username": "TEXT NOT NULL DEFAULT ''",
    "email": "TEXT",
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
    "rating": "rating",
    "paid": "purchase_cents",
    "added": "created_at",
}

COLUMNS = ("artist", "title", "year", "label", "format", "condition", "notes", "value_cents")
PURCHASE_COLUMNS = ("purchase_cents", "purchase_date", "purchased_from")
LIVE = "deleted_at IS NULL"


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path, timeout=10)
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
        conn.execute("PRAGMA journal_mode = WAL")  # readers don't block the lookup worker
        conn.executescript(SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(albums)")}
        for column, ddl in MIGRATIONS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE albums ADD COLUMN {column} {ddl}")  # fixed names
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        for column, ddl in USER_MIGRATIONS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE users ADD COLUMN {column} {ddl}")  # fixed names
        # One account per email address, ignoring letter case.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email ON users (lower(email))"
            " WHERE email IS NOT NULL"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS ix_albums_owner ON albums (owner_id, deleted_at)")
        # Lookups run in the background; any left 'pending' were cut off by a restart.
        conn.execute("UPDATE albums SET discogs_status = 'error' WHERE discogs_status = 'pending'")
        conn.execute(
            "UPDATE wishlist SET discogs_status = 'error' WHERE discogs_status = 'pending'"
        )


# --- Users and one-time links --------------------------------------------------------------


def count_users(path: Path) -> int:
    with connect(path) as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create_user(
    path: Path,
    username: str,
    password_hash: str,
    role: str = "member",
    email: str | None = None,
) -> int:
    with connect(path) as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, email) VALUES (?, ?, ?, ?)",
            (username, password_hash, role, email),
        )
        return cur.lastrowid


def email_in_use(path: Path, email: str, except_user: int | None = None) -> bool:
    with connect(path) as conn:
        return bool(
            conn.execute(
                "SELECT 1 FROM users WHERE lower(email) = lower(?) AND id != ?",
                (email, except_user or 0),
            ).fetchone()
        )


def get_user(path: Path, user_id: int):
    with connect(path) as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_name(path: Path, username: str):
    with connect(path) as conn:
        return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def list_users(path: Path):
    with connect(path) as conn:
        return conn.execute(
            "SELECT u.*, (SELECT COUNT(*) FROM albums a WHERE a.owner_id = u.id"
            " AND a.deleted_at IS NULL) AS album_count"
            " FROM users u ORDER BY u.username COLLATE NOCASE"
        ).fetchall()


def count_active_admins(path: Path) -> int:
    with connect(path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0"
        ).fetchone()[0]


def set_user_disabled(path: Path, user_id: int, disabled: bool) -> None:
    with connect(path) as conn:
        conn.execute(
            "UPDATE users SET disabled = ?, session_version = session_version + 1 WHERE id = ?",
            (1 if disabled else 0, user_id),
        )


def set_user_role(path: Path, user_id: int, role: str) -> None:
    with connect(path) as conn:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


def set_password(path: Path, user_id: int, password_hash: str) -> None:
    """Change a password and sign the user out of every other session."""
    with connect(path) as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, session_version = session_version + 1"
            " WHERE id = ?",
            (password_hash, user_id),
        )


def set_identity(path: Path, user_id: int, username: str, email: str | None) -> str:
    """Change username and email together.

    Returns '' on success, or 'username' / 'email' naming the value another account
    already uses (compared ignoring letter case).
    """
    with connect(path) as conn:
        if conn.execute(
            "SELECT 1 FROM users WHERE username = ? AND id != ?", (username, user_id)
        ).fetchone():
            return "username"
        if (
            email
            and conn.execute(
                "SELECT 1 FROM users WHERE lower(email) = lower(?) AND id != ?", (email, user_id)
            ).fetchone()
        ):
            return "email"
        try:
            conn.execute(
                "UPDATE users SET username = ?, email = ? WHERE id = ?",
                (username, email, user_id),
            )
        except sqlite3.IntegrityError:  # lost a race with another change
            return "username"
    return ""


def set_profile(path: Path, user_id: int, profile: dict) -> None:
    with connect(path) as conn:
        conn.execute(
            "UPDATE users SET display_name = ?, bio = ?, theme = ?, discogs_username = ?"
            " WHERE id = ?",
            (
                profile["display_name"],
                profile["bio"],
                profile["theme"],
                profile["discogs_username"],
                user_id,
            ),
        )


def bump_session_version(path: Path, user_id: int) -> None:
    with connect(path) as conn:
        conn.execute(
            "UPDATE users SET session_version = session_version + 1 WHERE id = ?", (user_id,)
        )


def count_albums(path: Path, owner_id: int) -> int:
    with connect(path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM albums WHERE owner_id = ?", (owner_id,)
        ).fetchone()[0]


def touch_login(path: Path, user_id: int) -> None:
    with connect(path) as conn:
        conn.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))


def adopt_orphan_albums(path: Path, owner_id: int) -> None:
    """Albums from before multi-user belong to the first admin."""
    with connect(path) as conn:
        conn.execute("UPDATE albums SET owner_id = ? WHERE owner_id IS NULL", (owner_id,))


def create_link(
    path: Path,
    token_hash: str,
    kind: str,
    created_by: int,
    days: int,
    role: str = "member",
    user_id: int | None = None,
) -> None:
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO invites (token_hash, kind, role, user_id, created_by, expires_at)"
            " VALUES (?, ?, ?, ?, ?, datetime('now', ?))",
            (token_hash, kind, role, user_id, created_by, f"+{int(days)} days"),
        )


def get_link(path: Path, token_hash: str):
    """A one-time link that is unused and unexpired, else None."""
    with connect(path) as conn:
        return conn.execute(
            "SELECT * FROM invites WHERE token_hash = ? AND used_at IS NULL"
            " AND expires_at > CURRENT_TIMESTAMP",
            (token_hash,),
        ).fetchone()


def use_link(path: Path, link_id: int) -> bool:
    """Mark a link used; False if someone else used it first."""
    with connect(path) as conn:
        cur = conn.execute(
            "UPDATE invites SET used_at = CURRENT_TIMESTAMP WHERE id = ? AND used_at IS NULL",
            (link_id,),
        )
        return cur.rowcount == 1


def list_open_links(path: Path):
    with connect(path) as conn:
        return conn.execute(
            "SELECT i.*, u.username AS target FROM invites i LEFT JOIN users u ON u.id = i.user_id"
            " WHERE i.used_at IS NULL AND i.expires_at > CURRENT_TIMESTAMP"
            " ORDER BY i.created_at DESC"
        ).fetchall()


def revoke_link(path: Path, link_id: int) -> None:
    with connect(path) as conn:
        conn.execute("DELETE FROM invites WHERE id = ? AND used_at IS NULL", (link_id,))


# --- Filters -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Filters:
    q: str = ""
    genre: str = ""
    decade: int | None = None
    format: str = ""
    condition: str = ""
    min_rating: float | None = None
    min_value: int | None = None  # cents
    max_value: int | None = None

    @property
    def active(self) -> bool:
        return any(v not in ("", None) for v in self.__dict__.values())


def _like(text: str) -> str:
    """LIKE pattern that matches `text` literally."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _where(owner_id: int, f: Filters) -> tuple[str, list]:
    clauses, params = ["owner_id = ?", LIVE], [owner_id]
    if f.q:
        clauses.append(
            "(artist LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' OR label LIKE ? ESCAPE '\\')"
        )
        params += [_like(f.q)] * 3
    if f.genre:
        clauses.append("('|' || genres || '|' || styles || '|') LIKE ? ESCAPE '\\'")
        params.append(_like(f"|{f.genre}|"))
    if f.decade is not None:
        clauses.append("year BETWEEN ? AND ?")
        params += [f.decade, f.decade + 9]
    if f.format:
        clauses.append("format = ?")
        params.append(f.format)
    if f.condition:
        clauses.append("condition = ?")
        params.append(f.condition)
    if f.min_rating is not None:
        clauses.append("rating >= ?")
        params.append(f.min_rating)
    if f.min_value is not None:
        clauses.append("value_cents >= ?")
        params.append(f.min_value)
    if f.max_value is not None:
        clauses.append("value_cents <= ?")
        params.append(f.max_value)
    return " AND ".join(clauses), params


# --- Albums ------------------------------------------------------------------------------


def list_albums(
    path: Path,
    owner_id: int,
    filters: Filters = Filters(),  # noqa: B008 (frozen dataclass, safe default)
    sort: str = "artist",
    direction: str = "asc",
):
    order = SORTS.get(sort, SORTS["artist"])
    dir_sql = "DESC" if direction == "desc" else "ASC"
    where, params = _where(owner_id, filters)
    sql = (
        f"SELECT * FROM albums WHERE {where} ORDER BY {order} {dir_sql},"  # noqa: S608
        " artist COLLATE NOCASE, title COLLATE NOCASE"  # (where/order are allowlisted)
    )
    with connect(path) as conn:
        return conn.execute(sql, params).fetchall()


def totals(path: Path, owner_id: int, filters: Filters = Filters()) -> dict:  # noqa: B008
    """Count, value, range, and purchase totals for the (filtered) collection."""
    where, params = _where(owner_id, filters)
    # `where` holds only fixed clauses from _where(); all values are bound parameters.
    sql = (
        "SELECT COUNT(*) AS count, COALESCE(SUM(value_cents), 0) AS value,"  # noqa: S608
        " COALESCE(SUM(value_low_cents), 0) AS low,"
        " COALESCE(SUM(value_high_cents), 0) AS high,"
        " COALESCE(SUM(purchase_cents), 0) AS paid,"
        " COALESCE(SUM(CASE WHEN purchase_cents IS NOT NULL AND value_cents IS NOT NULL"
        "   THEN value_cents - purchase_cents END), 0) AS gain,"
        " COUNT(CASE WHEN purchase_cents IS NOT NULL AND value_cents IS NOT NULL"
        "   THEN 1 END) AS gain_count"
        " FROM albums WHERE " + where
    )
    with connect(path) as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row)


def filter_options(path: Path, owner_id: int) -> dict:
    """Values present in this collection, for the filter menus."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT genres, styles, year FROM albums WHERE owner_id = ? AND deleted_at IS NULL",
            (owner_id,),
        ).fetchall()
    genres = sorted(
        {g for r in rows for g in (r["genres"] + "|" + r["styles"]).split("|") if g},
        key=str.casefold,
    )
    decades = sorted({r["year"] // 10 * 10 for r in rows if r["year"]})
    return {"genres": genres, "decades": decades}


def get_album(path: Path, album_id: int, include_deleted: bool = False):
    if include_deleted:
        sql = "SELECT * FROM albums WHERE id = ?"
    else:
        sql = "SELECT * FROM albums WHERE id = ? AND deleted_at IS NULL"
    with connect(path) as conn:
        return conn.execute(sql, (album_id,)).fetchone()


def create_album(
    path: Path,
    owner_id: int,
    data: dict,
    release_id: int | None = None,
    identifier: str = "",
    rating: float | None = None,
) -> int:
    """Insert an album. A `release_id` pins the pressing (barcode match or import)."""
    source = None if data["value_cents"] is None else "manual"
    purchase = [data.get(c) for c in PURCHASE_COLUMNS]
    purchase[2] = purchase[2] or ""
    with connect(path) as conn:
        cur = conn.execute(
            "INSERT INTO albums (owner_id, artist, title, year, label, format, condition, notes,"
            " value_cents, value_source, discogs_release_id, pressing_locked, identifier,"
            " rating, purchase_cents, purchase_date, purchased_from)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                owner_id,
                *(data[c] for c in COLUMNS),
                source,
                release_id,
                1 if release_id else 0,
                identifier,
                rating,
                *purchase,
            ],
        )
        return cur.lastrowid


def update_album(path: Path, album_id: int, data: dict, value_source: str | None) -> bool:
    with connect(path) as conn:
        cur = conn.execute(
            "UPDATE albums SET artist = ?, title = ?, year = ?, label = ?, format = ?,"
            " condition = ?, notes = ?, value_cents = ?, value_source = ?,"
            " purchase_cents = ?, purchase_date = ?, purchased_from = ?,"
            " updated_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
            [
                *(data[c] for c in COLUMNS),
                value_source,
                *(data.get(c) for c in PURCHASE_COLUMNS[:2]),
                data.get("purchased_from") or "",
                album_id,
            ],
        )
        return cur.rowcount == 1


def album_for_release(path: Path, owner_id: int, release_id: int):
    """A live album of this owner already pinned to this release (import de-duplication)."""
    with connect(path) as conn:
        return conn.execute(
            "SELECT * FROM albums WHERE owner_id = ? AND discogs_release_id = ?"
            " AND pressing_locked = 1 AND deleted_at IS NULL",
            (owner_id, release_id),
        ).fetchone()


def soft_delete(path: Path, album_id: int) -> bool:
    with connect(path) as conn:
        cur = conn.execute(
            "UPDATE albums SET deleted_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
            (album_id,),
        )
        return cur.rowcount == 1


def restore(path: Path, album_id: int) -> bool:
    with connect(path) as conn:
        cur = conn.execute(
            "UPDATE albums SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL",
            (album_id,),
        )
        return cur.rowcount == 1


def purge(path: Path, album_id: int) -> str | None:
    """Permanently delete a trashed album; returns its local cover file name, if any."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT cover_file FROM albums WHERE id = ? AND deleted_at IS NOT NULL", (album_id,)
        ).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
        return row["cover_file"] or ""


def list_trash(path: Path, owner_id: int):
    with connect(path) as conn:
        return conn.execute(
            "SELECT * FROM albums WHERE owner_id = ? AND deleted_at IS NOT NULL"
            " ORDER BY deleted_at DESC",
            (owner_id,),
        ).fetchall()


def trash_due(path: Path, days: int) -> list[int]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT id FROM albums WHERE deleted_at IS NOT NULL"
            " AND deleted_at < datetime('now', ?)",
            (f"-{int(days)} days",),
        ).fetchall()
    return [r["id"] for r in rows]


def set_cover(path: Path, album_id: int, uri: str) -> None:
    with connect(path) as conn:
        conn.execute("UPDATE albums SET cover_uri = ? WHERE id = ?", (uri, album_id))


def set_cover_file(path: Path, album_id: int, filename: str | None) -> None:
    with connect(path) as conn:
        conn.execute("UPDATE albums SET cover_file = ? WHERE id = ?", (filename, album_id))


def set_pressing(path: Path, album_id: int, release_id: int | None) -> None:
    """Pin the album to a pressing you picked, or (None) go back to automatic matching."""
    with connect(path) as conn:
        if release_id is None:
            conn.execute("UPDATE albums SET pressing_locked = 0 WHERE id = ?", (album_id,))
        else:
            conn.execute(
                "UPDATE albums SET discogs_release_id = ?, pressing_locked = 1 WHERE id = ?",
                (release_id, album_id),
            )


# --- Ratings, reviews, notes ---------------------------------------------------------------


def set_review(path: Path, album_id: int, rating: float | None, review: str) -> None:
    with connect(path) as conn:
        conn.execute(
            "UPDATE albums SET rating = ?, review = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (rating, review, album_id),
        )


def get_track_ratings(path: Path, album_id: int) -> dict[str, sqlite3.Row]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM track_ratings WHERE album_id = ?", (album_id,)
        ).fetchall()
    return {row["position"]: row for row in rows}


def save_track_ratings(path: Path, album_id: int, tracks: list[tuple]) -> None:
    """Replace all track ratings: tracks = [(position, title, rating, note), ...]."""
    with connect(path) as conn:
        conn.execute("DELETE FROM track_ratings WHERE album_id = ?", (album_id,))
        conn.executemany(
            "INSERT INTO track_ratings (album_id, position, title, rating, note)"
            " VALUES (?, ?, ?, ?, ?)",
            [(album_id, *t) for t in tracks if t[2] is not None or t[3]],
        )


def add_note(path: Path, album_id: int, author_id: int, body: str) -> None:
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO album_notes (album_id, author_id, body) VALUES (?, ?, ?)",
            (album_id, author_id, body),
        )


def list_notes(path: Path, album_id: int):
    with connect(path) as conn:
        return conn.execute(
            "SELECT n.*, u.username AS author FROM album_notes n"
            " LEFT JOIN users u ON u.id = n.author_id"
            " WHERE n.album_id = ? ORDER BY n.created_at DESC, n.id DESC",
            (album_id,),
        ).fetchall()


def delete_note(path: Path, album_id: int, note_id: int) -> bool:
    with connect(path) as conn:
        cur = conn.execute(
            "DELETE FROM album_notes WHERE id = ? AND album_id = ?", (note_id, album_id)
        )
        return cur.rowcount == 1


# --- Discogs data --------------------------------------------------------------------------


def set_discogs_status(path: Path, album_id: int, status: str) -> None:
    with connect(path) as conn:
        conn.execute(
            "UPDATE albums SET discogs_status = ?, discogs_checked_at = CURRENT_TIMESTAMP"
            " WHERE id = ?",
            (status, album_id),
        )


def _names(release: dict, key: str) -> str:
    value = release.get(key)
    items = (
        [v.replace("|", "/")[:60] for v in value if isinstance(v, str)]
        if isinstance(value, list)
        else []
    )
    return "|".join(items[:10])


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
            " year = COALESCE(year, ?), genres = ?, styles = ?"
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
                _names(release, "genres"),
                _names(release, "styles"),
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
        conn.execute(
            "INSERT INTO value_history (album_id, release_id, value_cents, low_cents,"
            " median_cents, high_cents)"
            " SELECT id, ?, value_cents, ?, ?, ? FROM albums WHERE id = ?",
            (e.release_id, e.low_cents, e.median_cents, e.high_cents, album_id),
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


def get_history(path: Path, album_id: int) -> list[sqlite3.Row]:
    with connect(path) as conn:
        return conn.execute(
            "SELECT * FROM value_history WHERE album_id = ? ORDER BY checked_at, id",
            (album_id,),
        ).fetchall()


def due_for_refresh(path: Path, days: int, limit: int) -> list[int]:
    """Live albums whose Discogs data is older than `days` (oldest first)."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT id FROM albums WHERE deleted_at IS NULL"
            " AND COALESCE(discogs_status, '') != 'pending'"
            " AND (discogs_checked_at IS NULL OR discogs_checked_at < datetime('now', ?))"
            " ORDER BY discogs_checked_at IS NOT NULL, discogs_checked_at LIMIT ?",
            (f"-{int(days)} days", int(limit)),
        ).fetchall()
    return [row["id"] for row in rows]


# --- Wishlist ------------------------------------------------------------------------------


def create_wish(path: Path, owner_id: int, data: dict, release_id: int | None = None) -> int:
    with connect(path) as conn:
        cur = conn.execute(
            "INSERT INTO wishlist (owner_id, artist, title, year, notes, target_cents,"
            " discogs_release_id, pressing_locked) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                owner_id,
                data["artist"],
                data["title"],
                data.get("year"),
                data.get("notes", ""),
                data.get("target_cents"),
                release_id,
                1 if release_id else 0,
            ),
        )
        return cur.lastrowid


def list_wishes(path: Path, owner_id: int):
    with connect(path) as conn:
        return conn.execute(
            "SELECT * FROM wishlist WHERE owner_id = ?"
            " ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE",
            (owner_id,),
        ).fetchall()


def get_wish(path: Path, wish_id: int):
    with connect(path) as conn:
        return conn.execute("SELECT * FROM wishlist WHERE id = ?", (wish_id,)).fetchone()


def wish_for_release(path: Path, owner_id: int, release_id: int):
    with connect(path) as conn:
        return conn.execute(
            "SELECT * FROM wishlist WHERE owner_id = ? AND discogs_release_id = ?",
            (owner_id, release_id),
        ).fetchone()


def set_wish_status(path: Path, wish_id: int, status: str) -> None:
    with connect(path) as conn:
        conn.execute(
            "UPDATE wishlist SET discogs_status = ?, checked_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, wish_id),
        )


def save_wish_price(
    path: Path, wish_id: int, release_id: int, low_cents: int | None, num_for_sale: int | None
) -> None:
    with connect(path) as conn:
        conn.execute(
            "UPDATE wishlist SET discogs_release_id = ?, low_cents = ?, num_for_sale = ?,"
            " discogs_status = 'done', checked_at = CURRENT_TIMESTAMP WHERE id = ?",
            (release_id, low_cents, num_for_sale, wish_id),
        )


def delete_wish(path: Path, wish_id: int) -> bool:
    with connect(path) as conn:
        return conn.execute("DELETE FROM wishlist WHERE id = ?", (wish_id,)).rowcount == 1


def wishes_due(path: Path, days: int, limit: int) -> list[int]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT id FROM wishlist WHERE COALESCE(discogs_status, '') != 'pending'"
            " AND (checked_at IS NULL OR checked_at < datetime('now', ?))"
            " ORDER BY checked_at IS NOT NULL, checked_at LIMIT ?",
            (f"-{int(days)} days", int(limit)),
        ).fetchall()
    return [row["id"] for row in rows]


# --- Stats -------------------------------------------------------------------------------


def stats(path: Path, owner_id: int, filters: Filters = Filters()) -> dict:  # noqa: B008
    """Breakdowns of the (filtered) collection for the stats page."""
    albums = list_albums(path, owner_id, filters)

    def group(key_fn):
        out: dict[str, dict] = {}
        for a in albums:
            for key in key_fn(a):
                g = out.setdefault(key, {"label": key, "count": 0, "value": 0})
                g["count"] += 1
                g["value"] += a["value_cents"] or 0
        return sorted(out.values(), key=lambda g: (-g["count"], g["label"].casefold()))

    by_condition = group(lambda a: [a["condition"]])
    rank = {c: i for i, c in enumerate(CONDITIONS)}
    by_condition.sort(key=lambda g: rank.get(g["label"], 99))
    by_decade = group(lambda a: [f"{a['year'] // 10 * 10}s"] if a["year"] else ["Unknown"])
    by_decade.sort(key=lambda g: g["label"])
    ratings = group(lambda a: [str(a["rating"])] if a["rating"] is not None else [])
    ratings.sort(key=lambda g: -float(g["label"]))
    return {
        "by_genre": group(lambda a: [g for g in a["genres"].split("|") if g] or ["Unknown"]),
        "by_decade": by_decade,
        "by_format": group(lambda a: [a["format"]]),
        "by_condition": by_condition,
        "by_label": group(lambda a: [a["label"]] if a["label"] else [])[:10],
        "by_rating": ratings,
        "top_value": sorted(
            (a for a in albums if a["value_cents"] is not None),
            key=lambda a: -a["value_cents"],
        )[:10],
    }
