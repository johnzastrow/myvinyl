"""Runtime settings, read from environment variables."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised when required settings are missing or invalid."""


@dataclass(frozen=True)
class Settings:
    # Secrets are excluded from repr so they never end up in logs or tracebacks.
    secret_key: str = field(repr=False)
    password_hash: str = field(repr=False)
    db_path: Path
    # Optional; raises the Discogs rate limit from 25 to 60 requests/min.
    discogs_token: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> "Settings":
        secret_key = os.environ.get("MYVINYL_SECRET_KEY", "")
        password_hash = os.environ.get("MYVINYL_PASSWORD_HASH", "")
        db_path = Path(os.environ.get("MYVINYL_DB", "myvinyl.db"))

        # Fail closed: refuse to start without a strong session key and a real Argon2id hash.
        if len(secret_key) < 32:
            raise ConfigError("MYVINYL_SECRET_KEY must be set (32+ characters).")
        if not password_hash.startswith("$argon2id$"):
            raise ConfigError("MYVINYL_PASSWORD_HASH must be an Argon2id hash.")
        discogs_token = os.environ.get("MYVINYL_DISCOGS_TOKEN", "").strip()
        if discogs_token and not re.fullmatch(r"[A-Za-z0-9]{20,100}", discogs_token):
            raise ConfigError("MYVINYL_DISCOGS_TOKEN does not look like a Discogs token.")
        return cls(
            secret_key=secret_key,
            password_hash=password_hash,
            db_path=db_path,
            discogs_token=discogs_token,
        )
