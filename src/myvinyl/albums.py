"""Album, wishlist, and account field definitions and form validation.

Invalid input is always rejected with an error message, never silently corrected.
"""

import datetime
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

FORMATS = ["LP", "2xLP", "EP", '7"', '10"', '12" single', "Box set"]
# Goldmine grading scale, best to worst.
CONDITIONS = ["M", "NM", "VG+", "VG", "G+", "G", "F", "P"]
FIELDS = [
    "artist",
    "title",
    "year",
    "label",
    "format",
    "condition",
    "notes",
    "value",
    "purchase_price",
    "purchase_date",
    "purchased_from",
]

MAX_LEN = {"artist": 200, "title": 200, "label": 200, "notes": 2000, "purchased_from": 200}
MAX_VALUE = Decimal("1000000")
MAX_NOTE = 2000


def format_money(cents: int | None) -> str:
    if cents is None:
        return ""
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def cents_to_text(cents: int | None) -> str:
    return "" if cents is None else f"{cents / 100:.2f}"


def parse_money(text: str) -> tuple[int | None, str]:
    """'$1,234.50' -> (123450, ''). Blank is (None, '')."""
    text = text.strip()
    if not text:
        return None, ""
    try:
        value = Decimal(text.removeprefix("$").replace(",", ""))
    except InvalidOperation:
        value = None
    if value is None or not value.is_finite() or not 0 <= value <= MAX_VALUE:
        return None, "Enter an amount between 0 and 1,000,000."
    if value.as_tuple().exponent < -2:
        return None, "Use at most two decimal places."
    return int(value * 100), ""


def parse_year(text: str) -> tuple[int | None, str]:
    text = text.strip()
    if not text:
        return None, ""
    max_year = datetime.date.today().year + 1
    if not re.fullmatch(r"[0-9]{4}", text) or not 1900 <= int(text) <= max_year:
        return None, f"Enter a year between 1900 and {max_year}."
    return int(text), ""


def parse_date(text: str) -> tuple[str | None, str]:
    """A past or present date as YYYY-MM-DD (what <input type=date> sends)."""
    text = text.strip()
    if not text:
        return None, ""
    try:
        day = datetime.date.fromisoformat(text)
    except ValueError:
        return None, "Enter a date as YYYY-MM-DD."
    if not datetime.date(1900, 1, 1) <= day <= datetime.date.today():
        return None, "Enter a date between 1900 and today."
    return day.isoformat(), ""


def album_to_values(album: Mapping) -> dict[str, str]:
    """Convert a stored album row into form field strings."""
    values = {
        f: "" if album[f] is None else str(album[f])
        for f in FIELDS
        if f not in ("value", "purchase_price")
    }
    values["value"] = cents_to_text(album["value_cents"])
    values["purchase_price"] = cents_to_text(album["purchase_cents"])
    return values


def parse_album(form: Mapping) -> tuple[dict, dict[str, str], dict[str, str]]:
    """Validate submitted form data.

    Returns (cleaned data for the database, raw values for re-rendering, errors).
    """
    values = {f: str(form.get(f, "")).strip() for f in FIELDS}
    errors: dict[str, str] = {}

    for field in ("artist", "title"):
        if not values[field]:
            errors[field] = "Required."
    for field, limit in MAX_LEN.items():
        if len(values[field]) > limit:
            errors[field] = f"Maximum {limit} characters."

    year, error = parse_year(values["year"])
    if error:
        errors["year"] = error
    if values["format"] not in FORMATS:
        errors["format"] = "Choose a format."
    if values["condition"] not in CONDITIONS:
        errors["condition"] = "Choose a condition."
    value_cents, error = parse_money(values["value"])
    if error:
        errors["value"] = error
    purchase_cents, error = parse_money(values["purchase_price"])
    if error:
        errors["purchase_price"] = error
    purchase_date, error = parse_date(values["purchase_date"])
    if error:
        errors["purchase_date"] = error

    data = {
        "artist": values["artist"],
        "title": values["title"],
        "year": year,
        "label": values["label"],
        "format": values["format"],
        "condition": values["condition"],
        "notes": values["notes"],
        "value_cents": value_cents,
        "purchase_cents": purchase_cents,
        "purchase_date": purchase_date,
        "purchased_from": values["purchased_from"],
    }
    return data, values, errors


WISH_FIELDS = ["artist", "title", "year", "notes", "target"]


def parse_wish(form: Mapping) -> tuple[dict, dict[str, str], dict[str, str]]:
    values = {f: str(form.get(f, "")).strip() for f in WISH_FIELDS}
    errors: dict[str, str] = {}
    for field in ("artist", "title"):
        if not values[field]:
            errors[field] = "Required."
        elif len(values[field]) > 200:
            errors[field] = "Maximum 200 characters."
    if len(values["notes"]) > 500:
        errors["notes"] = "Maximum 500 characters."
    year, error = parse_year(values["year"])
    if error:
        errors["year"] = error
    target, error = parse_money(values["target"])
    if error:
        errors["target"] = error
    data = {
        "artist": values["artist"],
        "title": values["title"],
        "year": year,
        "notes": values["notes"],
        "target_cents": target,
    }
    return data, values, errors


# --- Accounts ------------------------------------------------------------------------------

MIN_PASSWORD = 12
MAX_PASSWORD = 256
ROLES = ["member", "admin"]


def parse_username(raw) -> tuple[str, str]:
    text = str(raw or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{3,32}", text):
        return "", "Use 3 to 32 letters, digits, dots, dashes, or underscores."
    return text, ""


THEMES = {
    "auto": "Automatic (royal by day, navy at night)",
    "royal": "Royal blue",
    "navy": "Navy",
    "light": "Light",
    "gray": "Gray",
}


def parse_profile(form: Mapping) -> tuple[dict, dict[str, str]]:
    profile = {
        "display_name": " ".join(str(form.get("display_name", "")).split()),
        "bio": str(form.get("bio", "")).strip(),
        "theme": str(form.get("theme", "auto")),
        "discogs_username": str(form.get("discogs_username", "")).strip(),
    }
    errors = {}
    if len(profile["display_name"]) > 60:
        errors["display_name"] = "Maximum 60 characters."
    if len(profile["bio"]) > 1000:
        errors["bio"] = "Maximum 1000 characters."
    if profile["theme"] not in THEMES:
        errors["theme"] = "Choose a theme."
    if profile["discogs_username"] and not re.fullmatch(
        r"[\w.\-]{1,100}", profile["discogs_username"]
    ):
        errors["discogs_username"] = "That doesn't look like a Discogs username."
    return profile, errors


def check_new_password(password, confirm) -> str:
    """Return an error message, or '' if the new password is acceptable."""
    if not isinstance(password, str) or not isinstance(confirm, str):
        return "Enter the password twice."
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if len(password) > MAX_PASSWORD:
        return f"Use at most {MAX_PASSWORD} characters."
    if password != confirm:
        return "The passwords do not match."
    return ""


# --- Ratings and reviews -------------------------------------------------------------------

RATINGS = [x / 2 for x in range(1, 11)]  # 0.5, 1.0, ... 5.0
MAX_REVIEW = 5000
MAX_TRACK_NOTE = 500


def parse_rating(raw) -> tuple[float | None, str]:
    """Return (rating or None, error). Blank means 'not rated'."""
    text = str(raw or "").strip()
    if not text:
        return None, ""
    try:
        value = float(text)
    except ValueError:
        return None, "Choose a rating from 0.5 to 5 stars."
    if value not in RATINGS:
        return None, "Choose a rating from 0.5 to 5 stars."
    return value, ""


def format_stars(rating: float | None) -> str:
    """3.5 -> '★★★½☆' (text, so it needs no images or scripts)."""
    if rating is None:
        return ""
    full = int(rating)
    half = rating - full >= 0.5
    return "★" * full + ("½" if half else "") + "☆" * (5 - full - (1 if half else 0))


# --- Barcode / catalog number --------------------------------------------------------------

MAX_IDENTIFIER = 50


def parse_identifier(raw) -> tuple[str, str]:
    """Return (identifier, error). Allows digits, letters, spaces, and - . / only."""
    text = " ".join(str(raw or "").split())
    if not text:
        return "", ""
    if len(text) > MAX_IDENTIFIER or not all(c.isalnum() or c in " -./" for c in text):
        return "", "Enter a barcode or catalog number (letters, digits, spaces, - . /)."
    return text, ""
