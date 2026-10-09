"""Passwords, one-time link tokens, and login rate limiting."""

import hashlib
import secrets
import threading
import time
from collections import deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()
# Verified against when a username doesn't exist, so a login takes the same time either way
# and response timing doesn't reveal which usernames exist.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and bool(password_hash)
    except (VerificationError, InvalidHashError):
        return False


def new_link_token() -> tuple[str, str]:
    """(token for the URL, SHA-256 hash to store). Tokens have 256 bits of entropy."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LoginLimiter:
    """Blocks a key (IP or username) after too many failures in a sliding window."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 900) -> None:
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque[float] | None:
        attempts = self._failures.get(key)
        if attempts is None:
            return None
        while attempts and now - attempts[0] > self.window:
            attempts.popleft()
        if not attempts:
            del self._failures[key]  # don't keep entries for every key ever tried
            return None
        return attempts

    def blocked(self, *keys: str) -> bool:
        with self._lock:
            now = time.monotonic()
            return any(
                (a := self._recent(k, now)) is not None and len(a) >= self.max_failures
                for k in keys
            )

    def record_failure(self, *keys: str) -> None:
        with self._lock:
            now = time.monotonic()
            for key in keys:
                attempts = self._recent(key, now)
                if attempts is None:
                    attempts = self._failures[key] = deque()
                attempts.append(now)

    def reset(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)
