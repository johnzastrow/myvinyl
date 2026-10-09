import urllib.error

import pytest

from myvinyl import discogs


class FakeClient:
    """Serves canned JSON keyed by path; a list value is consumed one response per call."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        value = self.responses[path]
        if isinstance(value, Exception):
            raise value
        return value.pop(0) if isinstance(value, list) else value


def result(release_id, year, fmt=("Vinyl", "LP"), title="New Order - Substance"):
    return {
        "id": release_id,
        "title": title,
        "year": str(year),
        "format": list(fmt),
        "label": ["Factory", "Warner Music UK Ltd."],
        "country": "UK",
        "catno": "Fact 200",
    }


def stats(value, count=3, currency="USD"):
    price = None if value is None else {"value": value, "currency": currency}
    return {"lowest_price": price, "num_for_sale": count}


def test_enrich_ranks_prices_and_fetches_release():
    client = FakeClient(
        {
            "/database/search": {
                "results": [
                    result(1, 1987),
                    result(2, 2024, fmt=("Vinyl", "Unofficial Release")),
                    result(3, 2023),
                    result(4, 2023),
                ]
            },
            "/marketplace/stats/3": stats(24.48),
            "/marketplace/stats/4": stats(72.28),
            "/marketplace/stats/1": stats(18),
            "/marketplace/stats/2": stats(None, 0),
            "/releases/3": {"id": 3, "title": "Substance"},
        }
    )
    e = discogs.enrich(client, "New order", "Substance", 2024)

    # Closest official year wins; unofficial pressings rank last.
    assert [p.release_id for p in e.pressings] == [3, 4, 1, 2]
    assert e.release_id == 3 and e.value_cents == 2448
    assert (e.low_cents, e.median_cents, e.high_cents) == (1800, 2448, 7228)
    assert e.release == {"id": 3, "title": "Substance"}
    assert e.pressings[0].label == "Factory"  # label only, not company credits
    search_params = client.calls[0][1]
    assert search_params["artist"] == "New order" and search_params["release_title"] == "Substance"


def test_value_falls_back_to_median_when_best_match_not_for_sale():
    client = FakeClient(
        {
            "/database/search": {"results": [result(1, 2000), result(2, 1990), result(3, 1980)]},
            "/marketplace/stats/1": stats(None, 0),
            "/marketplace/stats/2": stats(10),
            "/marketplace/stats/3": stats(30),
            "/releases/1": {},
        }
    )
    e = discogs.enrich(client, "A", "Substance", 2000)
    assert e.release_id == 1 and e.value_cents == 2000  # median of 10 and 30


def test_caps_pressings_checked():
    results = [result(i, 2000) for i in range(1, 30)]
    responses = {"/database/search": {"results": results}, "/releases/1": {}}
    responses.update({f"/marketplace/stats/{i}": stats(i) for i in range(1, 30)})
    client = FakeClient(responses)
    e = discogs.enrich(client, "A", "Substance", None)
    assert len(e.pressings) == discogs.MAX_PRESSINGS
    stat_calls = [c for c in client.calls if c[0].startswith("/marketplace")]
    assert len(stat_calls) == discogs.MAX_PRESSINGS


def test_one_failed_price_does_not_sink_lookup():
    client = FakeClient(
        {
            "/database/search": {"results": [result(1, 2000), result(2, 2000)]},
            "/marketplace/stats/1": urllib.error.URLError("timeout"),
            "/marketplace/stats/2": stats(5),
            "/releases/1": {},
        }
    )
    e = discogs.enrich(client, "A", "Substance", None)
    assert e.pressings[0].price_cents is None and e.value_cents == 500


def test_ignores_results_with_a_different_title():
    # Regression: free-text search once matched "Jail - Broken Glass" for Substance.
    client = FakeClient(
        {"/database/search": {"results": [result(9, 2024, title="Jail - Broken Glass")]}}
    )
    assert discogs.enrich(client, "New order", "Substance", 2024) is None


def test_rejects_unexpected_price_data():
    for bad in (
        {"value": 5, "currency": "EUR"},
        {"value": "5", "currency": "USD"},
        {"value": -1, "currency": "USD"},
        {"value": True, "currency": "USD"},
    ):
        assert discogs._price_cents(bad) is None
    assert discogs._price_cents({"value": 19.99, "currency": "USD"}) == 1999


def test_search_errors_propagate_for_caller_to_handle():
    client = FakeClient({"/database/search": urllib.error.URLError("offline")})
    with pytest.raises(discogs.LOOKUP_ERRORS):
        discogs.enrich(client, "A", "B", None)


def test_release_summary_is_defensive():
    summary = discogs.release_summary(
        {
            "title": "Substance",
            "year": 2023,
            "formats": [{"name": "Vinyl", "qty": "2", "descriptions": ["LP"], "text": "180g"}],
            "labels": [{"name": "Factory", "catno": "Fact 200"}, "junk"],
            "tracklist": [
                {"position": "A1", "title": "Ceremony"},
                {"title": "Side A", "type_": "heading"},
            ],
            "images": [{"uri": "javascript:alert(1)"}, {"uri": "https://i.discogs.com/a.jpg"}],
            "community": {"have": "lots", "rating": {"average": 4.5, "count": 2}},
            "genres": ["Electronic", 5],
        }
    )
    assert summary["formats"] == ["2xVinyl, LP, 180g"]
    assert summary["labels"] == [{"name": "Factory", "catno": "Fact 200"}]
    assert [t["title"] for t in summary["tracks"]] == ["Ceremony"]
    assert summary["cover"] == "https://i.discogs.com/a.jpg"
    assert summary["have"] is None and summary["rating"] == 4.5
    assert summary["genres"] == ["Electronic"]
    assert discogs.release_summary({})["tracks"] == []


def test_client_sends_token_header(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b"{}"

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["ua"] = req.get_header("User-agent")
        return Resp()

    monkeypatch.setattr(discogs.urllib.request, "urlopen", fake_urlopen)
    discogs.DiscogsClient("abc123" * 5).get("/releases/1")
    assert seen["url"] == "https://api.discogs.com/releases/1"
    assert seen["auth"] == "Discogs token=" + "abc123" * 5
    assert seen["ua"].startswith("myvinyl/")

    discogs.DiscogsClient().get("/releases/1")
    assert seen["auth"] is None


def test_enrich_with_pinned_release_not_in_search():
    client = FakeClient(
        {
            "/database/search": {"results": [result(1, 2000)]},
            "/marketplace/stats/77": stats(99),
            "/marketplace/stats/1": stats(10),
            "/releases/77": {
                "title": "Substance",
                "year": 2016,
                "country": "US",
                "artists": [{"name": "New Order (2)"}],
                "labels": [{"name": "Factory", "catno": "F 1"}],
                "formats": [{"name": "Vinyl", "descriptions": ["LP"]}],
            },
        }
    )
    e = discogs.enrich(client, "New Order", "Substance", None, release_id=77)
    assert e.release_id == 77 and e.value_cents == 9900
    chosen = e.pressings[0]
    assert (chosen.year, chosen.country, chosen.label, chosen.catno) == (
        2016,
        "US",
        "Factory",
        "F 1",
    )
    assert chosen.title == "New Order - Substance"
    assert (e.low_cents, e.high_cents) == (1000, 9900)


def test_find_by_identifier_barcode_then_catno():
    client = FakeClient(
        {
            "/database/search": [
                {"results": []},  # barcode: nothing
                {
                    "results": [
                        {
                            "id": 5,
                            "title": "Jail (17) - Broken Glass",
                            "year": "2024",
                            "format": ["Vinyl", '10"', "EP"],
                            "label": ["Dure"],
                            "catno": "none",
                            "country": "Canada",
                        }
                    ]
                },
            ]
        }
    )
    found = discogs.find_by_identifier(client, "0 12345-678")
    assert client.calls[0][1]["barcode"] == "012345678"
    assert client.calls[1][1]["catno"] == "0 12345-678"
    assert found[0]["artist"] == "Jail" and found[0]["title"] == "Broken Glass"
    assert found[0]["format"] == '10"' and found[0]["year"] == 2024


def test_map_format():
    assert discogs.map_format(["Vinyl", "LP"], 1) == "LP"
    assert discogs.map_format(["Vinyl", "LP"], 2) == "2xLP"
    assert discogs.map_format(["Vinyl", '7"', "Single"], 1) == '7"'
    assert discogs.map_format(["Vinyl", '12"', "Maxi-Single"], 1) == '12" single'
    assert discogs.map_format(["Box Set", "Vinyl", "LP"], 5) == "Box set"
    assert discogs.map_format(["Vinyl", "EP"], 1) == "EP"


def test_collection_pages_and_filters_non_vinyl():
    page1 = {
        "pagination": {"pages": 2},
        "releases": [
            {
                "rating": 4,
                "basic_information": {
                    "id": 1,
                    "title": "Closer",
                    "year": 1980,
                    "artists": [{"name": "Joy Division"}],
                    "labels": [{"name": "Factory"}],
                    "formats": [{"name": "Vinyl", "qty": "1", "descriptions": ["LP"]}],
                },
            },
            {
                "basic_information": {
                    "id": 2,
                    "title": "A CD",
                    "artists": [{"name": "X"}],
                    "formats": [{"name": "CD"}],
                }
            },
        ],
    }
    page2 = {"pagination": {"pages": 2}, "releases": ["junk", {"basic_information": "junk"}]}
    client = FakeClient(
        {
            "/oauth/identity": {"username": "vinylfan"},
            "/users/vinylfan/collection/folders/0/releases": [page1, page2],
        }
    )
    items = list(discogs.collection(client, discogs.identity(client)))
    assert [i["release_id"] for i in items] == [1]
    assert items[0]["rating"] == 4.0 and items[0]["format"] == "LP"


def test_identity_rejects_odd_usernames():
    client = FakeClient({"/oauth/identity": {"username": "../../admin"}})
    with pytest.raises(ValueError):
        discogs.identity(client)
