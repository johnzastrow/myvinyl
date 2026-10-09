"""Album field definitions and form validation."""

import datetime
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

FORMATS = ["LP", "2xLP", "EP", '7"', '10"', '12" single', "Box set"]
# Goldmine grading scale, best to worst.
CONDITIONS = ["M", "NM", "VG+", "VG", "G+", "G", "F", "P"]
FIELDS = ["artist", "title", "year", "label", "format", "condition", "notes", "value"]

MAX_LEN = {"artist": 200, "title": 200, "label": 200, "notes": 2000}
MAX_VALUE = Decimal("1000000")


def format_money(cents: int | None) -> str:
    if cents is None:
        return ""
    return f"${cents / 100:,.2f}"


def album_to_values(album: Mapping) -> dict[str, str]:
    """Convert a stored album row into form field strings."""
    values = {f: "" if album[f] is None else str(album[f]) for f in FIELDS if f != "value"}
    cents = album["value_cents"]
    values["value"] = "" if cents is None else f"{cents / 100:.2f}"
    return values


def parse_album(form: Mapping) -> tuple[dict, dict[str, str], dict[str, str]]:
    """Validate submitted form data.

    Returns (cleaned data for the database, raw values for re-rendering, errors).
    Invalid input is rejected with an error, never silently corrected.
    """
    values = {f: str(form.get(f, "")).strip() for f in FIELDS}
    errors: dict[str, str] = {}

    for field in ("artist", "title"):
        if not values[field]:
            errors[field] = "Required."
    for field, limit in MAX_LEN.items():
        if len(values[field]) > limit:
            errors[field] = f"Maximum {limit} characters."

    year = None
    if values["year"]:
        max_year = datetime.date.today().year + 1
        if not values["year"].isdigit() or not 1900 <= int(values["year"]) <= max_year:
            errors["year"] = f"Enter a year between 1900 and {max_year}."
        else:
            year = int(values["year"])

    if values["format"] not in FORMATS:
        errors["format"] = "Choose a format."
    if values["condition"] not in CONDITIONS:
        errors["condition"] = "Choose a condition."

    value_cents = None
    if values["value"]:
        try:
            value = Decimal(values["value"].removeprefix("$").replace(",", ""))
        except InvalidOperation:
            value = None
        if value is None or not value.is_finite() or not 0 <= value <= MAX_VALUE:
            errors["value"] = "Enter an amount between 0 and 1,000,000."
        elif value.as_tuple().exponent < -2:
            errors["value"] = "Use at most two decimal places."
        else:
            value_cents = int(value * 100)

    data = {
        "artist": values["artist"],
        "title": values["title"],
        "year": year,
        "label": values["label"],
        "format": values["format"],
        "condition": values["condition"],
        "notes": values["notes"],
        "value_cents": value_cents,
    }
    return data, values, errors
