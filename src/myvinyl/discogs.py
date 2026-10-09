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


def _pressing_from_result(r: dict) -> Pressing | None:
    if not isinstance(r, dict) or not isinstance(r.get("id"), int) or r["id"] <= 0:
        return None
    return Pressing(
        release_id=r["id"],
        title=_text(r.get("title")),
        year=_int_year(r.get("year")),
        country=_text(r.get("country"), 100),
        # Search results list the label first, then every company credit; keep the label.
        label=next(iter(_str_list(r.get("label"))), "")[:200],
        catno=_text(r.get("catno"), 100),
        formats=", ".join(_str_list(r.get("format")))[:200],
    )


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
        pressing = _pressing_from_result(r)
        if pressing is None or wanted not in _normalize(_text(r.get("title"), 500)):
            continue
        unofficial = "Unofficial Release" in pressing.formats
        distance = abs(pressing.year - year) if year and pressing.year else 10_000
        ranked.append(((unofficial, distance, position), pressing))
    ranked.sort(key=lambda item: item[0])  # ties keep Discogs relevance order
    return [p for _, p in ranked]


def find_by_identifier(client: DiscogsClient, identifier: str) -> list[dict]:
    """Vinyl releases matching a barcode, falling back to catalog number.

    Returns form-ready dicts (artist, title, year, label, format) plus pressing details.
    A barcode can match several pressings (for example black and colored vinyl), so the
    caller lets you choose when there is more than one.
    """
    base = {"type": "release", "format": "Vinyl", "per_page": "25"}
    digits = re.sub(r"[\s-]", "", identifier)
    attempts = [{"barcode": digits}] if digits.isdigit() else []
    attempts.append({"catno": identifier})
    for extra in attempts:
        results = client.get("/database/search", {**base, **extra}).get("results")
        if not isinstance(results, list):
            continue
        found = []
        for r in results:
            pressing = _pressing_from_result(r)
            if pressing is None:
                continue
            artist, title = split_title(pressing.title)
            qty = _int_or_none(r.get("format_quantity"))
            found.append(
                {
                    "release_id": pressing.release_id,
                    "artist": artist,
                    "title": title,
                    "year": pressing.year,
                    "label": pressing.label,
                    "format": map_format(_str_list(r.get("format")), qty),
                    "country": pressing.country,
                    "catno": pressing.catno,
                    "formats": pressing.formats,
                }
            )
        if found:
            return found
    return []


def price_pressing(client: DiscogsClient, pressing: Pressing) -> Pressing:
    stats = client.get(f"/marketplace/stats/{int(pressing.release_id)}")
    count = _int_or_none(stats.get("num_for_sale"))
    return replace(
        pressing,
        price_cents=_price_cents(stats.get("lowest_price")),
        num_for_sale=count if count is not None and count >= 0 else None,
    )


def _pressing_from_release(release: dict, fallback: Pressing) -> Pressing:
    """Fill in any missing pressing details from its full release record."""
    labels = [lbl for lbl in release.get("labels") or [] if isinstance(lbl, dict)]
    names = []
    for f in release.get("formats") or []:
        if isinstance(f, dict):
            names += [_text(f.get("name"), 50), *_str_list(f.get("descriptions"))]
    artists = [a.get("name") for a in release.get("artists") or [] if isinstance(a, dict)]
    artist = clean_artist(next((a for a in artists if isinstance(a, str)), ""))
    title = _text(release.get("title"))
    return replace(
        fallback,
        title=fallback.title or (f"{artist} - {title}" if artist else title),
        year=fallback.year or _int_year(release.get("year")),
        country=fallback.country or _text(release.get("country"), 100),
        label=fallback.label or (_text(labels[0].get("name")) if labels else ""),
        catno=fallback.catno or (_text(labels[0].get("catno"), 100) if labels else ""),
        formats=fallback.formats or ", ".join(n for n in names if n)[:200],
    )


def enrich(
    client: DiscogsClient,
    artist: str,
    title: str,
    year: int | None,
    release_id: int | None = None,
) -> Enrichment | None:
    """Full lookup. Returns None on no match; raises LOOKUP_ERRORS on API/network failure.

    With `release_id` (a pressing you picked), that pressing is the match, and the range
    still covers the other pressings found by artist and title.
    """
    candidates = find_pressings(client, artist, title, year)
    if release_id is not None:
        pinned = next((p for p in candidates if p.release_id == release_id), None)
        if pinned is None:
            pinned = Pressing(release_id, "", None, "", "", "", "")
        candidates = [pinned] + [p for p in candidates if p.release_id != release_id]
    candidates = candidates[:MAX_PRESSINGS]
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

    release = client.get(f"/releases/{int(pressings[0].release_id)}")
    pressings[0] = chosen = _pressing_from_release(release, pressings[0])
    prices = sorted(p.price_cents for p in pressings if p.price_cents is not None)
    median = int(statistics.median(prices)) if prices else None
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


# --- Discogs collection import -------------------------------------------------------------


def identity(client: DiscogsClient) -> str:
    """Username of the token's owner (requires a token)."""
    username = client.get("/oauth/identity").get("username")
    if not isinstance(username, str) or not re.fullmatch(r"[\w.\-]{1,100}", username):
        raise ValueError("Discogs did not return a username")
    return username


USERNAME_RE = re.compile(r"[\w.\-]{1,100}")


def valid_username(username: str) -> bool:
    return bool(USERNAME_RE.fullmatch(username or ""))


def _user_releases(client: DiscogsClient, url: str, list_key: str, max_pages: int):
    """Yield (item, basic_information) for each vinyl release in a paged user list."""
    page, pages = 1, 1
    while page <= min(pages, max_pages):
        data = client.get(url, {"per_page": "100", "page": str(page)})
        pagination = data.get("pagination") if isinstance(data.get("pagination"), dict) else {}
        pages = _int_or_none(pagination.get("pages")) or 1
        items = data.get(list_key) if isinstance(data.get(list_key), list) else []
        for item in items:
            info = item.get("basic_information") if isinstance(item, dict) else None
            if isinstance(info, dict) and isinstance(info.get("id"), int) and info["id"] > 0:
                yield item, info
        page += 1


def _release_fields(info: dict) -> dict | None:
    """Form-ready fields from a collection/wantlist entry, or None if it isn't vinyl."""
    artists = [a.get("name") for a in info.get("artists") or [] if isinstance(a, dict)]
    labels = [lbl for lbl in info.get("labels") or [] if isinstance(lbl, dict)]
    names, qty = [], None
    for f in info.get("formats") or []:
        if isinstance(f, dict):
            names += [_text(f.get("name"), 50), *_str_list(f.get("descriptions"))]
            qty = qty or _int_year(f.get("qty"))
    title = _text(info.get("title"))
    if "Vinyl" not in names or not title:
        return None  # myvinyl only tracks vinyl
    return {
        "release_id": info["id"],
        "artist": clean_artist(next((a for a in artists if isinstance(a, str)), ""))
        or "Unknown artist",
        "title": title,
        "year": _int_year(info.get("year")),
        "label": _text(labels[0].get("name")) if labels else "",
        "format": map_format(names, qty),
    }


def collection(client: DiscogsClient, username: str, max_pages: int = 50):
    """Yield form-ready dicts for every vinyl release in a Discogs collection.

    Works for your own collection with your token, or anyone's public collection.
    """
    if not valid_username(username):
        raise ValueError("Invalid Discogs username")
    user = urllib.parse.quote(username, safe="")
    url = f"/users/{user}/collection/folders/0/releases"
    for item, info in _user_releases(client, url, "releases", max_pages):
        fields = _release_fields(info)
        if fields:
            rating = _int_or_none(item.get("rating"))
            fields["rating"] = float(rating) if rating and 1 <= rating <= 5 else None
            yield fields


def wantlist(client: DiscogsClient, username: str, max_pages: int = 20):
    """Yield form-ready dicts for every vinyl release in a Discogs wantlist."""
    if not valid_username(username):
        raise ValueError("Invalid Discogs username")
    user = urllib.parse.quote(username, safe="")
    for item, info in _user_releases(client, f"/users/{user}/wants", "wants", max_pages):
        fields = _release_fields(info)
        if fields:
            fields["notes"] = _text(item.get("notes"), 500)
            yield fields


def price_wish(
    client: DiscogsClient, artist: str, title: str, year: int | None, release_id: int | None
) -> tuple[int, int | None, int | None] | None:
    """(release_id, lowest asking price, copies for sale) for a wishlist item, or None.

    Uses the pressing you picked if there is one, else the best match: two API calls.
    """
    if release_id is None:
        candidates = find_pressings(client, artist, title, year)
        if not candidates:
            return None
        release_id = candidates[0].release_id
    priced = price_pressing(client, Pressing(release_id, "", None, "", "", "", ""))
    return release_id, priced.price_cents, priced.num_for_sale


# --- Cover images ------------------------------------------------------------------------

MAX_IMAGE_BYTES = 5_000_000
IMAGE_TYPES = {  # magic bytes -> file extension
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"RIFF": "webp",  # checked further below
    b"GIF87a": "gif",
    b"GIF89a": "gif",
}


def image_extension(data: bytes) -> str | None:
    """File extension for a real JPEG/PNG/WebP/GIF, judged by content, not headers."""
    for magic, ext in IMAGE_TYPES.items():
        if data.startswith(magic):
            if ext == "webp" and data[8:12] != b"WEBP":
                return None
            return ext
    return None


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


_NO_REDIRECTS = urllib.request.build_opener(_RefuseRedirects)


def fetch_image(url: str) -> tuple[bytes, str]:
    """Download a Discogs image. Only https://i.discogs.com/ URLs are allowed.

    Returns (bytes, extension); raises ValueError for anything that isn't a small image.
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "i.discogs.com" or parsed.port:
        raise ValueError("Not a Discogs image URL")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 (checked)
    # No redirects: a redirect could point the request anywhere (including internal
    # addresses) before we got a chance to check it.
    with _NO_REDIRECTS.open(req, timeout=TIMEOUT_SECONDS) as resp:
        data = resp.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image too large")
    ext = image_extension(data)
    if ext is None:
        raise ValueError("Not a supported image")
    return data, ext


# --- Mapping Discogs values to myvinyl fields ----------------------------------------------


def clean_artist(name: str) -> str:
    """Drop Discogs disambiguation suffixes: 'Jail (17)' -> 'Jail', 'Madonna*' -> 'Madonna'."""
    return re.sub(r"\s*\(\d+\)$", "", name.strip()).rstrip("*").strip()[:200]


def split_title(full: str) -> tuple[str, str]:
    """Split a search-result title 'Artist - Album' into its parts."""
    artist, sep, title = full.partition(" - ")
    return (clean_artist(artist), title.strip()[:200]) if sep else ("", full.strip()[:200])


def map_format(names: list[str], qty: int | None) -> str:
    """Best-effort mapping of Discogs format names onto myvinyl's format list."""
    if "Box Set" in names:
        return "Box set"
    if '7"' in names:
        return '7"'
    if '10"' in names:
        return '10"'
    if '12"' in names and ("Single" in names or "Maxi-Single" in names):
        return '12" single'
    if "EP" in names and "LP" not in names:
        return "EP"
    if (qty or 1) >= 2 or names.count("LP") >= 2:
        return "2xLP"
    return "LP"


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


class DiscogsService:
    """The Discogs operations the app uses, bundled so tests can swap in a fake."""

    def __init__(self, client: DiscogsClient, has_token: bool) -> None:
        self.client = client
        self.has_token = has_token

    def enrich(self, artist, title, year, release_id=None) -> Enrichment | None:
        return enrich(self.client, artist, title, year, release_id)

    def find_by_identifier(self, identifier: str) -> list[dict]:
        return find_by_identifier(self.client, identifier)

    def collection(self, username: str):
        yield from collection(self.client, username)

    def wantlist(self, username: str):
        yield from wantlist(self.client, username)

    def price_wish(self, artist, title, year, release_id=None):
        return price_wish(self.client, artist, title, year, release_id)

    def fetch_image(self, url: str) -> tuple[bytes, str]:
        return fetch_image(url)
