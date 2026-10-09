"""Runtime settings, read from environment variables."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised when required settings are missing or invalid."""


def _int_env(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    if not re.fullmatch(r"[0-9]{1,5}", raw) or not low <= int(raw) <= high:
        raise ConfigError(f"{name} must be a whole number from {low} to {high}.")
    return int(raw)


@dataclass(frozen=True)
class Settings:
    # Secrets are excluded from repr so they never end up in logs or tracebacks.
    secret_key: str = field(repr=False)
    db_path: Path
    # Only used to create the first admin account when the database has no users yet.
    password_hash: str = field(default="", repr=False)
    admin_username: str = "admin"
    # Shared Discogs token for the whole server (optional; raises the rate limit).
    discogs_token: str = field(default="", repr=False)
    # Re-check Discogs prices for albums older than this many days (0 turns it off).
    refresh_days: int = 7
    backup_hours: int = 24  # 0 turns automatic backups off
    backup_keep: int = 14
    trash_days: int = 30  # trashed albums are purged after this many days (0 = never)
    # Public address used in invite links, e.g. https://myvinyl.fluidgrid.site
    base_url: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        secret_key = os.environ.get("MYVINYL_SECRET_KEY", "")
        password_hash = os.environ.get("MYVINYL_PASSWORD_HASH", "").strip()
        db_path = Path(os.environ.get("MYVINYL_DB", "myvinyl.db"))

        # Fail closed: refuse to start without a strong session key.
        if len(secret_key) < 32:
            raise ConfigError("MYVINYL_SECRET_KEY must be set (32+ characters).")
        if password_hash and not password_hash.startswith("$argon2id$"):
            raise ConfigError("MYVINYL_PASSWORD_HASH must be an Argon2id hash.")
        admin_username = os.environ.get("MYVINYL_ADMIN_USER", "admin").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.\-]{3,32}", admin_username):
            raise ConfigError("MYVINYL_ADMIN_USER must be 3 to 32 letters, digits, . _ or -.")
        discogs_token = os.environ.get("MYVINYL_DISCOGS_TOKEN", "").strip()
        if discogs_token and not re.fullmatch(r"[A-Za-z0-9]{20,100}", discogs_token):
            raise ConfigError("MYVINYL_DISCOGS_TOKEN does not look like a Discogs token.")
        base_url = os.environ.get("MYVINYL_BASE_URL", "").strip().rstrip("/")
        if base_url and not re.fullmatch(r"https?://[A-Za-z0-9.\-]+(:[0-9]{1,5})?", base_url):
            raise ConfigError("MYVINYL_BASE_URL must look like https://myvinyl.example.com")
        return cls(
            secret_key=secret_key,
            password_hash=password_hash,
            db_path=db_path,
            admin_username=admin_username,
            discogs_token=discogs_token,
            refresh_days=_int_env("MYVINYL_REFRESH_DAYS", 7, 0, 365),
            backup_hours=_int_env("MYVINYL_BACKUP_HOURS", 24, 0, 24 * 30),
            backup_keep=_int_env("MYVINYL_BACKUP_KEEP", 14, 1, 1000),
            trash_days=_int_env("MYVINYL_TRASH_DAYS", 30, 0, 3650),
            base_url=base_url,
        )
