"""Discogs enrichment: value range across pressings plus release metadata.

For an album, myvinyl searches Discogs for matching vinyl pressings, checks the lowest
current asking price of the top matches, picks the best-matching pressing automatically,
and fetches that pressing's full release record. Matching is automatic, so the chosen
pressing can differ from the one you own; the range covers all checked pressings.

Works without a token (25 requests/min). An optional personal token raises the limit to
60/min, so lookups finish about twice as fast.
"""

import json
import logging
import re
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from decimal import Decimal

from . import __version__

API = "https://api.discogs.com"  # fixed host; paths are built only from integers
USER_AGENT = f"myvinyl/{__version__}"  # Discogs requires a descriptive User-Agent
TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 2_000_000
MAX_PRESSINGS = 10  # pressings priced per lookup (one API call each)
SEARCH_PAGE_SIZE = 25
IMAGE_PREFIX = "https://i.discogs.com/"  # the only image host we render

log = logging.getLogger("myvinyl.discogs")

LOOKUP_ERRORS = (urllib.error.URLError, TimeoutError, ValueError, OSError)


class DiscogsClient:
    """Minimal JSON client with a process-wide request throttle and one 429 retry."""

    def __init__(self, token: str = "") -> None:
        self._token = token
        # Stay just under the documented per-minute limits (60 with a token, 25 without).
        self._min_interval = 60 / (55 if token else 23)
        self._lock = threading.Lock()
        self._next_at = 0.0

    def _wait_turn(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            self._next_at = max(now, self._next_at) + self._min_interval
        if wait > 0:
            time.sleep(wait)

    def get(self, path: str, params: dict[str, str] | None = None) -> dict:
        url = API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"User-Agent": USER_AGENT}
        if self._token:
            headers["Authorization"] = f"Discogs token={self._token}"
        for attempt in range(2):
            self._wait_turn()
            req = urllib.request.Request(url, headers=headers)  # noqa: S310 (fixed https host)
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310
                    body = resp.read(MAX_RESPONSE_BYTES + 1)
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt == 0:
                    log.info("Discogs rate limit hit; backing off")
                    time.sleep(30)
                    continue
                raise
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("Discogs response too large")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("Unexpected Discogs response")
        return data


@dataclass(frozen=True)
class Pressing:
    release_id: int
    title: str
    year: int | None
    country: str
    label: str
    catno: str
    formats: str
    price_cents: int | None = None
    num_for_sale: int | None = None


@dataclass
class Enrichment:
    release_id: int  # the pressing chosen as the best match
    value_cents: int | None
    low_cents: int | None
    high_cents: int | None
    median_cents: int | None
    pressings: list[Pressing] = field(default_factory=list)
    release: dict = field(default_factory=dict)  # full Discogs release record


# --- Parsing helpers -----------------------------------------------------------------------


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.casefold()).split())


def _int_year(value) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value) or None
    return None


def _text(value, limit: int = 200) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _str_list(value) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _int_or_none(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _price_cents(price) -> int | None:
    if not isinstance(price, dict) or price.get("currency") != "USD":
        return None
    value = price.get("value")
    if not isinstance(value, int | float) or isinstance(value, bool) or not 0 <= value <= 1_000_000:
        return None
    return int((Decimal(str(value)) * 100).quantize(Decimal("1")))


# --- Lookup steps ------------------------------------------------------------------------


def find_pressings(
    client: DiscogsClient, artist: str, title: str, year: int | None
) -> list[Pressing]:
    """Return matching vinyl pressings, best match first.

    Uses Discogs' structured artist/title search (free-text search returns unrelated
    records when nothing matches exactly), keeps only results whose title contains the
    album title, then ranks official pressings from the year closest to the one entered.
    """
    params = {
        "artist": artist,
        "release_title": title,
        "type": "release",
        "format": "Vinyl",
        "per_page": str(SEARCH_PAGE_SIZE),
    }
    results = client.get("/database/search", params).get("results")
    if not isinstance(results, list):
        return []

    wanted = _normalize(title)
    ranked = []
    for position, r in enumerate(results):
        if not isinstance(r, dict) or not isinstance(r.get("id"), int) or r["id"] <= 0:
            continue
        if wanted not in _normalize(_text(r.get("title"), 500)):
            continue
        formats = _str_list(r.get("format"))
        r_year = _int_year(r.get("year"))
        unofficial = "Unofficial Release" in formats
        distance = abs(r_year - year) if year and r_year else 10_000
        pressing = Pressing(
            release_id=r["id"],
            title=_text(r.get("title")),
            year=r_year,
            country=_text(r.get("country"), 100),
            # Search results list the label first, then every company credit; keep the label.
            label=next(iter(_str_list(r.get("label"))), "")[:200],
            catno=_text(r.get("catno"), 100),
            formats=", ".join(formats)[:200],
        )
        ranked.append(((unofficial, distance, position), pressing))
    ranked.sort(key=lambda item: item[0])  # ties keep Discogs relevance order
    return [p for _, p in ranked]


def price_pressing(client: DiscogsClient, pressing: Pressing) -> Pressing:
    stats = client.get(f"/marketplace/stats/{int(pressing.release_id)}")
    count = _int_or_none(stats.get("num_for_sale"))
    return replace(
        pressing,
        price_cents=_price_cents(stats.get("lowest_price")),
        num_for_sale=count if count is not None and count >= 0 else None,
    )


def enrich(client: DiscogsClient, artist: str, title: str, year: int | None) -> Enrichment | None:
    """Full lookup. Returns None on no match; raises LOOKUP_ERRORS on API/network failure."""
    candidates = find_pressings(client, artist, title, year)[:MAX_PRESSINGS]
    if not candidates:
        log.info("no Discogs match for %r / %r", artist, title)
        return None

    pressings = []
    for pressing in candidates:
        try:
            pressings.append(price_pressing(client, pressing))
        except LOOKUP_ERRORS as exc:  # one failed price shouldn't sink the whole lookup
            log.warning("Discogs price lookup failed for %s: %s", pressing.release_id, exc)
            pressings.append(pressing)

    prices = sorted(p.price_cents for p in pressings if p.price_cents is not None)
    median = int(statistics.median(prices)) if prices else None
    chosen = pressings[0]
    release = client.get(f"/releases/{int(chosen.release_id)}")
    return Enrichment(
        release_id=chosen.release_id,
        # The chosen pressing's price; if it has none for sale, fall back to the range median.
        value_cents=chosen.price_cents if chosen.price_cents is not None else median,
        low_cents=prices[0] if prices else None,
        high_cents=prices[-1] if prices else None,
        median_cents=median,
        pressings=pressings,
        release=release,
    )


# --- Release record -> display data --------------------------------------------------------


def release_images(release: dict) -> list[dict]:
    """All album-art images on a release (front, back, labels, inserts...).

    Only URLs on the Discogs image host are kept, so a tampered record can't make the
    page load images from anywhere else.
    """
    images = []
    raw = release.get("images") if isinstance(release.get("images"), list) else []
    for image in raw:
        if not isinstance(image, dict):
            continue
        uri, thumb = image.get("uri"), image.get("uri150")
        if not (isinstance(uri, str) and uri.startswith(IMAGE_PREFIX)):
            continue
        if not (isinstance(thumb, str) and thumb.startswith(IMAGE_PREFIX)):
            thumb = uri
        images.append(
            {"uri": uri[:1000], "thumb": thumb[:1000], "type": _text(image.get("type"), 20)}
        )
    return images


def release_summary(release: dict) -> dict:
    """Extract the fields shown on the album page. Every value is type-checked."""

    def dicts(key):
        value = release.get(key)
        return [i for i in value if isinstance(i, dict)] if isinstance(value, list) else []

    formats = []
    for f in dicts("formats"):
        qty = _text(f.get("qty"), 10)
        name = (f"{qty}x" if qty and qty != "1" else "") + _text(f.get("name"), 50)
        extras = _str_list(f.get("descriptions")) + (
            [_text(f["text"], 100)] if f.get("text") else []
        )
        formats.append(", ".join([name, *extras]))

    community = release.get("community") if isinstance(release.get("community"), dict) else {}
    rating = community.get("rating") if isinstance(community.get("rating"), dict) else {}
    average = rating.get("average")

    images = release_images(release)

    return {
        "title": _text(release.get("title")),
        "artists": [a["name"] for a in dicts("artists") if isinstance(a.get("name"), str)],
        "year": _int_year(release.get("year")),
        "released": _text(release.get("released_formatted") or release.get("released"), 50),
        "country": _text(release.get("country"), 100),
        "labels": [
            {"name": lbl["name"], "catno": _text(lbl.get("catno"), 100)}
            for lbl in dicts("labels")
            if isinstance(lbl.get("name"), str)
        ],
        "formats": formats,
        "genres": _str_list(release.get("genres")),
        "styles": _str_list(release.get("styles")),
        "tracks": [
            {
                "position": _text(t.get("position"), 10),
                "title": _text(t.get("title")),
                "duration": _text(t.get("duration"), 10),
            }
            for t in dicts("tracklist")
            if t.get("type_", "track") == "track"
        ],
        "credits": [
            {"name": a["name"], "role": _text(a.get("role"))}
            for a in dicts("extraartists")
            if isinstance(a.get("name"), str)
        ][:40],
        "identifiers": [
            {"type": _text(i.get("type"), 50), "value": i["value"][:200]}
            for i in dicts("identifiers")
            if isinstance(i.get("value"), str)
        ][:20],
        "notes": _text(release.get("notes"), 4000),
        "have": _int_or_none(community.get("have")),
        "want": _int_or_none(community.get("want")),
        "rating": average if isinstance(average, int | float) else None,
        "rating_count": _int_or_none(rating.get("count")),
        "images": images,
        # Default cover: the image Discogs marks primary, else the first one.
        "cover": next((i["uri"] for i in images if i["type"] == "primary"), "")
        or (images[0]["uri"] if images else ""),
    }
