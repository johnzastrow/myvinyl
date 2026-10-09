"""Runtime settings, read from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised when required settings are missing or invalid."""


@dataclass(frozen=True)
class Settings:
    secret_key: str
    password_hash: str
    db_path: Path

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
        return cls(secret_key=secret_key, password_hash=password_hash, db_path=db_path)
