"""Street-value lookup via the public Discogs API (no token required).

Value = the lowest current asking price on the Discogs marketplace for an automatically
chosen vinyl release. Matching is automatic, so it can pick a different pressing than the
one you own.
"""

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal

from . import __version__

API = "https://api.discogs.com"  # fixed host; only query parameters vary
USER_AGENT = f"myvinyl/{__version__}"  # Discogs requires a descriptive User-Agent
TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 2_000_000

log = logging.getLogger("myvinyl.discogs")


@dataclass(frozen=True)
class ValueLookup:
    release_id: int
    value_cents: int | None  # None when no copies are currently for sale


def _get(path: str, params: dict[str, str] | None = None) -> dict:
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 (https only)
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310
        body = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("Discogs response too large")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("Unexpected Discogs response")
    return data


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.casefold()).split())


def _year(result: dict) -> int | None:
    year = result.get("year")
    return int(year) if isinstance(year, str | int) and str(year).isdigit() else None


def find_release(artist: str, title: str, year: int | None) -> int | None:
    """Pick a vinyl release automatically.

    Uses Discogs' structured artist/title search (free-text search returns unrelated
    records when nothing matches exactly), keeps only results whose title contains the
    album title, then prefers official pressings from the year closest to the one entered.
    """
    params = {
        "artist": artist,
        "release_title": title,
        "type": "release",
        "format": "Vinyl",
        "per_page": "25",
    }
    results = _get("/database/search", params).get("results")
    if not isinstance(results, list):
        return None

    wanted = _normalize(title)
    candidates = [
        r
        for r in results
        if isinstance(r, dict)
        and isinstance(r.get("id"), int)
        and r["id"] > 0
        and wanted in _normalize(str(r.get("title", "")))
    ]
    if not candidates:
        return None

    def rank(item: tuple[int, dict]) -> tuple:
        position, r = item
        unofficial = "Unofficial Release" in (r.get("format") or [])
        r_year = _year(r)
        distance = abs(r_year - year) if year and r_year else 10_000
        return (unofficial, distance, position)  # ties keep Discogs relevance order

    return min(enumerate(candidates), key=rank)[1]["id"]


def lowest_price_cents(release_id: int) -> int | None:
    stats = _get(f"/marketplace/stats/{int(release_id)}")
    price = stats.get("lowest_price")
    if not isinstance(price, dict) or price.get("currency") != "USD":
        return None
    value = price.get("value")
    if not isinstance(value, int | float) or isinstance(value, bool) or not 0 <= value <= 1_000_000:
        return None
    return int((Decimal(str(value)) * 100).quantize(Decimal("1")))


def lookup_value(artist: str, title: str, year: int | None) -> ValueLookup | None:
    """Best-effort lookup. Returns None (and logs) on no match or any API/network error."""
    try:
        release_id = find_release(artist, title, year)
        if release_id is None:
            log.info("no Discogs match for %r / %r", artist, title)
            return None
        return ValueLookup(release_id, lowest_price_cents(release_id))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        log.warning("Discogs lookup failed: %s", exc)
        return None
