import urllib.error

from myvinyl import discogs


def fake_api(responses):
    """Return a _get replacement that serves canned JSON keyed by path."""
    calls = []

    def _get(path, params=None):
        calls.append((path, params))
        value = responses[path]
        return value.pop(0) if isinstance(value, list) else value

    return _get, calls


def test_prefers_closest_year_and_official_pressing(monkeypatch):
    get, calls = fake_api(
        {
            "/database/search": {
                "results": [
                    {
                        "id": 1,
                        "title": "New Order - Substance",
                        "year": "1987",
                        "format": ["Vinyl"],
                    },
                    {
                        "id": 2,
                        "title": "New Order - Substance",
                        "year": "2024",
                        "format": ["Vinyl", "Unofficial Release"],
                    },
                    {
                        "id": 3,
                        "title": "New Order - Substance",
                        "year": "2023",
                        "format": ["Vinyl"],
                    },
                ]
            },
            "/marketplace/stats/3": {"lowest_price": {"value": 24.48, "currency": "USD"}},
        }
    )
    monkeypatch.setattr(discogs, "_get", get)
    assert discogs.lookup_value("New order", "Substance", 2024) == discogs.ValueLookup(3, 2448)
    assert calls[0][1]["artist"] == "New order"
    assert calls[0][1]["release_title"] == "Substance"


def test_without_year_keeps_relevance_order(monkeypatch):
    get, _ = fake_api(
        {
            "/database/search": {
                "results": [
                    {"id": 7, "title": "A - B", "year": "1990"},
                    {"id": 8, "title": "A - B", "year": "2000"},
                ]
            },
            "/marketplace/stats/7": {"lowest_price": None},
        }
    )
    monkeypatch.setattr(discogs, "_get", get)
    assert discogs.lookup_value("A", "B", None) == discogs.ValueLookup(7, None)


def test_ignores_results_with_a_different_title(monkeypatch):
    # Regression: free-text search once matched "Jail - Broken Glass" for Substance.
    get, _ = fake_api(
        {
            "/database/search": {"results": [{"id": 9, "title": "Jail - Broken Glass"}]},
        }
    )
    monkeypatch.setattr(discogs, "_get", get)
    assert discogs.lookup_value("New order", "Substance", 2024) is None


def test_rejects_unexpected_price_data(monkeypatch):
    for bad in (
        {"value": 5, "currency": "EUR"},
        {"value": "5", "currency": "USD"},
        {"value": -1, "currency": "USD"},
        {"value": True, "currency": "USD"},
    ):
        get, _ = fake_api(
            {
                "/database/search": {"results": [{"id": 1, "title": "A - B"}]},
                "/marketplace/stats/1": {"lowest_price": bad},
            }
        )
        monkeypatch.setattr(discogs, "_get", get)
        assert discogs.lookup_value("A", "B", None).value_cents is None


def test_no_match_or_network_error_returns_none(monkeypatch):
    get, _ = fake_api({"/database/search": {"results": []}})
    monkeypatch.setattr(discogs, "_get", get)
    assert discogs.lookup_value("A", "B", None) is None

    def boom(path, params=None):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(discogs, "_get", boom)
    assert discogs.lookup_value("A", "B", None) is None
