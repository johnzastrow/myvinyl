"""Form validators: every rejection branch returns a message and no value."""

import datetime

import pytest

from myvinyl import albums

BASE = {"artist": "A", "title": "T", "format": "LP", "condition": "VG+"}


@pytest.mark.parametrize(
    ("text", "cents", "has_error"),
    [
        ("", None, False),
        ("$1,234.50", 123450, False),
        ("abc", None, True),
        ("-1", None, True),
        ("1000001", None, True),
        ("NaN", None, True),
        ("1.234", None, True),
    ],
)
def test_parse_money(text, cents, has_error):
    value, error = albums.parse_money(text)
    assert value == cents and bool(error) is has_error


def test_parse_date():
    assert albums.parse_date("") == (None, "")
    assert albums.parse_date("2020-02-29") == ("2020-02-29", "")
    assert albums.parse_date("29/02/2020")[1]
    future = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
    assert albums.parse_date(future)[1] and albums.parse_date("1899-12-31")[1]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("title", "x" * 201, "Maximum 200"),
        ("year", "1850", ""),
        ("format", "8-track", "Choose a format"),
        ("condition", "Great", "Choose a condition"),
        ("value", "abc", "amount"),
        ("purchase_price", "-5", "amount"),
        ("purchase_date", "yesterday", "YYYY-MM-DD"),
    ],
)
def test_parse_album_rejects(field, value, message):
    _, _, errors = albums.parse_album({**BASE, field: value})
    assert field in errors and message in errors[field]


@pytest.mark.parametrize(
    ("field", "value"),
    [("artist", ""), ("title", "x" * 201), ("notes", "n" * 501), ("year", "12"), ("target", "x")],
)
def test_parse_wish_rejects(field, value):
    _, _, errors = albums.parse_wish({"artist": "A", "title": "T", field: value})
    assert field in errors


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("display_name", "d" * 61),
        ("bio", "b" * 1001),
        ("theme", "neon"),
        ("discogs_username", "has spaces"),
    ],
)
def test_parse_profile_rejects(field, value):
    _, errors = albums.parse_profile({field: value})
    assert field in errors


@pytest.mark.parametrize(
    ("password", "confirm", "message"),
    [
        (None, None, "twice"),
        ("short", "short", "at least"),
        ("p" * 300, "p" * 300, "at most"),
        ("a long enough password", "a long enough passw0rd", "do not match"),
        ("a long enough password", "a long enough password", ""),
    ],
)
def test_check_new_password(password, confirm, message):
    error = albums.check_new_password(password, confirm)
    assert (message in error) if message else error == ""


@pytest.mark.parametrize(
    ("raw", "rating", "has_error"),
    [
        ("", None, False),
        ("4.5", 4.5, False),
        ("x", None, True),
        ("4.2", None, True),
        ("6", None, True),
    ],
)
def test_parse_rating(raw, rating, has_error):
    value, error = albums.parse_rating(raw)
    assert value == rating and bool(error) is has_error


def test_format_stars():
    assert albums.format_stars(None) == ""
    assert albums.format_stars(3.5) == "★★★½☆"
    assert albums.format_stars(5) == "★★★★★"
