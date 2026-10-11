"""Encryption for the Discogs tokens users store in their profiles.

Each token is sealed with AES-256-GCM under a server key (MYVINYL_TOKEN_KEY) that lives in
the environment, never in the database, so a copied database or backup doesn't reveal the
tokens. The user id is bound in as associated data, so a sealed token copied onto another
account fails to open.
"""

import base64
import binascii
import logging
import os
import re

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger("myvinyl.tokens")

KEY_BYTES = 32  # AES-256
NONCE_BYTES = 12  # the standard GCM nonce size; random per encryption
PREFIX = "v1:"  # format version, so the scheme can change later
TOKEN_RE = re.compile(r"[A-Za-z0-9]{20,100}")  # Discogs personal access tokens


def new_key() -> str:
    """A fresh random key, in the text form MYVINYL_TOKEN_KEY expects."""
    return base64.urlsafe_b64encode(os.urandom(KEY_BYTES)).decode("ascii")


def parse_key(text: str) -> bytes:
    """Decode MYVINYL_TOKEN_KEY; raises ValueError unless it is exactly 32 bytes."""
    try:
        key = base64.urlsafe_b64decode(text.strip().encode("ascii"))
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise ValueError("not base64") from exc
    if len(key) != KEY_BYTES:
        raise ValueError("wrong length")
    return key


def valid_token(token: str) -> bool:
    return bool(TOKEN_RE.fullmatch(token or ""))


class TokenBox:
    """Seals and opens Discogs tokens for one server key."""

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError("token key must be 32 bytes")
        self._aead = AESGCM(key)

    def __repr__(self) -> str:  # never show key material
        return "TokenBox()"

    @staticmethod
    def _aad(user_id: int) -> bytes:
        return f"myvinyl:discogs-token:user:{int(user_id)}".encode("ascii")

    def seal(self, user_id: int, token: str) -> str:
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._aead.encrypt(nonce, token.encode("ascii"), self._aad(user_id))
        return PREFIX + base64.urlsafe_b64encode(nonce + sealed).decode("ascii")

    def open(self, user_id: int, blob: str | None) -> str:
        """The stored token, or "" if there is none or it can't be opened.

        Failing to open means the key changed or the value was tampered with; the caller
        then treats the user as having no token (anonymous Discogs access).
        """
        if not blob:
            return ""
        try:
            if not blob.startswith(PREFIX):
                raise ValueError("unknown format")
            raw = base64.urlsafe_b64decode(blob[len(PREFIX) :].encode("ascii"))
            token = self._aead.decrypt(
                raw[:NONCE_BYTES], raw[NONCE_BYTES:], self._aad(user_id)
            ).decode("ascii")
        except (InvalidTag, ValueError, binascii.Error, UnicodeError):
            log.warning("stored Discogs token for user %s could not be decrypted", user_id)
            return ""
        return token if valid_token(token) else ""


def hint(token: str) -> str:
    """What the UI may show of a token: its last 4 characters."""
    return token[-4:] if token else ""
