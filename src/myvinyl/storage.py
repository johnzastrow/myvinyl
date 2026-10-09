"""Files on disk next to the database: saved cover images and automatic backups."""

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("myvinyl.storage")

COVER_EXTENSIONS = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}


class Storage:
    """Locations for covers and backups, derived from the database path."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        base = db_path.resolve().parent
        self.covers = base / "covers"
        self.backups = base / "backups"

    # --- Covers ---------------------------------------------------------------------------

    def save_cover(self, album_id: int, data: bytes, ext: str) -> str:
        """Write a cover atomically; returns the file name stored in the database."""
        if ext not in COVER_EXTENSIONS:
            raise ValueError("unsupported image type")
        self.covers.mkdir(parents=True, exist_ok=True)
        name = f"{int(album_id)}.{ext}"  # names are built from integers only
        tmp = self.covers / f".{name}.tmp"
        tmp.write_bytes(data)
        for old in self.covers.glob(f"{int(album_id)}.*"):
            if old.name != name:
                old.unlink(missing_ok=True)
        tmp.replace(self.covers / name)
        return name

    def cover_path(self, filename: str | None) -> Path | None:
        """Path for a stored cover name, refusing anything that isn't '<id>.<ext>'."""
        if not filename:
            return None
        stem, _, ext = filename.partition(".")
        if not stem.isascii() or not stem.isdigit() or ext not in COVER_EXTENSIONS:
            return None
        path = self.covers / filename
        return path if path.is_file() else None

    def delete_cover(self, filename: str | None) -> None:
        path = self.cover_path(filename)
        if path:
            path.unlink(missing_ok=True)

    # --- Backups --------------------------------------------------------------------------

    def backup(self, keep: int) -> Path:
        """Consistent copy of the live database (SQLite online backup); keeps the newest N."""
        self.backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        target = self.backups / f"myvinyl-{stamp}.db"
        src = sqlite3.connect(self.db_path)
        dst = sqlite3.connect(target)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
            src.close()
        for old in self.list_backups()[keep:]:
            old.unlink(missing_ok=True)
        log.info("backup written: %s", target.name)
        return target

    def list_backups(self) -> list[Path]:
        if not self.backups.is_dir():
            return []
        return sorted(self.backups.glob("myvinyl-*.db"), reverse=True)
