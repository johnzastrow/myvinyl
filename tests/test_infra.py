"""Background jobs, the Discogs HTTP client, and the command-line entry points."""

import io
import logging
import threading
import urllib.error

import pytest
from test_discogs import FakeClient, stats

from myvinyl import __main__ as entry
from myvinyl import credentials, discogs, tokens
from myvinyl.lookups import JobQueue, PeriodicTask

# --- JobQueue -------------------------------------------------------------------------------


def test_job_queue_runs_jobs_in_order_on_a_worker_thread():
    done, seen = threading.Event(), []

    def process(job):
        seen.append((job, threading.current_thread().name))
        if job == ("album", 3):
            done.set()

    q = JobQueue(process)
    q.start()
    q.start()  # starting twice is harmless
    for i in (1, 2, 3):
        q.submit(("album", i))
    assert done.wait(5)
    q.stop()
    assert [job for job, _ in seen] == [("album", 1), ("album", 2), ("album", 3)]
    assert {name for _, name in seen} == {"discogs-jobs"}


def test_job_queue_skips_duplicates_and_counts_pending():
    gate, started = threading.Event(), threading.Event()
    seen = []

    def process(job):
        started.set()
        gate.wait(5)
        seen.append(job)

    q = JobQueue(process)
    q.start()
    q.submit(("album", 1))
    assert started.wait(5)  # job 1 is running, so the next ones wait
    assert q.submit(("album", 2)) is True
    assert q.submit(("album", 2)) is False  # already waiting
    assert q.submit(("wish", 9)) is True
    assert q.pending() == 2 and q.pending("album") == 1 and q.pending("wish") == 1
    gate.set()
    q.stop()
    assert seen == [("album", 1), ("album", 2), ("wish", 9)]


def test_job_queue_survives_a_failing_job(caplog):
    done, seen = threading.Event(), []

    def process(job):
        if job == "bad":
            raise RuntimeError("boom")
        seen.append(job)
        done.set()

    q = JobQueue(process)
    q.start()
    with caplog.at_level(logging.ERROR):
        q.submit("bad")
        q.submit("good")
        assert done.wait(5)
    q.stop()
    assert seen == ["good"] and "background job failed" in caplog.text


def test_job_queue_inline_mode_runs_immediately():
    seen = []
    q = JobQueue(seen.append, inline=True)
    q.start()  # no thread in inline mode
    assert q.submit("x") is True and seen == ["x"]
    q.stop()


# --- PeriodicTask ---------------------------------------------------------------------------


def test_periodic_task_ticks_logs_and_stops(caplog):
    ticks = []
    twice = threading.Event()

    def tick():
        ticks.append(1)
        if len(ticks) == 1:
            raise RuntimeError("first tick fails")
        twice.set()
        return "did work"

    task = PeriodicTask("test-task", tick, interval_seconds=0.01, initial_delay=0)
    with caplog.at_level(logging.INFO, logger="myvinyl.lookups"):
        task.start()
        task.start()  # already running
        assert twice.wait(5)
        task.stop()
    assert "test-task failed" in caplog.text and "test-task: did work" in caplog.text
    count = len(ticks)
    threading.Event().wait(0.05)
    assert len(ticks) == count  # stopped


def test_periodic_task_stopped_during_initial_delay():
    ticks = []
    task = PeriodicTask("never", lambda: ticks.append(1), interval_seconds=1, initial_delay=60)
    task.start()
    task.stop()
    assert ticks == []
    task.stop()  # stopping twice is harmless


# --- Discogs HTTP client --------------------------------------------------------------------


class Resp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        return self.body[:n]


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(discogs.time, "sleep", slept.append)
    return slept


def test_client_retries_once_after_rate_limit(monkeypatch, no_sleep):
    calls = []

    def urlopen(req, timeout):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, None)
        return Resp(b'{"ok": 1}')

    monkeypatch.setattr(discogs.urllib.request, "urlopen", urlopen)
    assert discogs.DiscogsClient().get("/releases/1", {"a": "b"}) == {"ok": 1}
    assert len(calls) == 2 and calls[0].endswith("/releases/1?a=b")
    assert 30 in no_sleep


def test_client_gives_up_after_second_rate_limit_and_other_errors(monkeypatch, no_sleep):
    def always_429(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, None)

    monkeypatch.setattr(discogs.urllib.request, "urlopen", always_429)
    with pytest.raises(urllib.error.HTTPError):
        discogs.DiscogsClient().get("/releases/1")

    def not_found(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(discogs.urllib.request, "urlopen", not_found)
    with pytest.raises(urllib.error.HTTPError):
        discogs.DiscogsClient().get("/releases/1")


@pytest.mark.parametrize(
    "body",
    [b"x" * (discogs.MAX_RESPONSE_BYTES + 10), b"[1, 2]", b"not json"],
    ids=["too-large", "not-an-object", "not-json"],
)
def test_client_rejects_bad_responses(monkeypatch, no_sleep, body):
    monkeypatch.setattr(discogs.urllib.request, "urlopen", lambda req, timeout: Resp(body))
    with pytest.raises(ValueError):
        discogs.DiscogsClient().get("/releases/1")


def test_client_repr_hides_token():
    token = "Secret" * 5
    assert token not in repr(discogs.DiscogsClient(token))
    assert repr(discogs.DiscogsClient()) == "DiscogsClient(authenticated=False)"


def test_pacer_spaces_requests(monkeypatch):
    clock = [100.0]
    slept = []
    monkeypatch.setattr(discogs.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(discogs.time, "sleep", slept.append)
    pacer = discogs.Pacer(60)  # one per second
    pacer.wait_turn()
    pacer.wait_turn()
    pacer.wait_turn()
    assert slept == [pytest.approx(1.0), pytest.approx(2.0)]


def test_clients_share_one_pacer_per_kind():
    a, b = discogs.DiscogsClient("t" * 30), discogs.DiscogsClient("u" * 30)
    assert a._pacer is b._pacer is discogs.TOKEN_PACER
    assert discogs.DiscogsClient()._pacer is discogs.ANONYMOUS_PACER


# --- Discogs collection, wantlist, and wishlist prices --------------------------------------


def entry_item(release_id, fmt="Vinyl", **extra):
    return {
        "basic_information": {
            "id": release_id,
            "title": "Substance",
            "year": 1987,
            "artists": [{"name": "New Order (2)"}],
            "labels": [{"name": "Factory"}],
            "formats": [{"name": fmt, "qty": "2", "descriptions": ["LP"]}],
        },
        **extra,
    }


def test_collection_pages_filters_and_maps():
    client = FakeClient(
        {
            "/users/fan.one/collection/folders/0/releases": [
                {
                    "pagination": {"pages": 2},
                    "releases": [
                        entry_item(1, rating=4),
                        entry_item(2, fmt="CD"),  # not vinyl: skipped
                        {"basic_information": {"id": "x"}},  # malformed: skipped
                        "junk",
                    ],
                },
                {"pagination": {"pages": 2}, "releases": [entry_item(3, rating=9)]},
            ]
        }
    )
    with pytest.raises(ValueError):
        list(discogs.collection(client, "bad name!"))
    items = list(discogs.collection(client, "fan.one"))
    assert [i["release_id"] for i in items] == [1, 3]
    assert items[0]["artist"] == "New Order" and items[0]["rating"] == 4.0
    assert items[1]["rating"] is None  # out of range
    assert client.calls[1][1] == {"per_page": "100", "page": "2"}


def test_wantlist_maps_notes_and_respects_page_cap():
    page = {"pagination": {"pages": 99}, "wants": [entry_item(5, notes="<b>want</b>" * 100)]}
    client = FakeClient({"/users/fan/wants": [page, page]})
    with pytest.raises(ValueError):
        list(discogs.wantlist(client, ""))
    items = list(discogs.wantlist(client, "fan", max_pages=2))
    assert len(items) == 2 and len(items[0]["notes"]) <= 500
    assert len(client.calls) == 2  # stopped at max_pages despite 99 pages


def test_price_wish_pinned_and_searched():
    client = FakeClient({"/marketplace/stats/7": stats(12.5, count=4)})
    assert discogs.price_wish(client, "A", "B", None, 7) == (7, 1250, 4)

    client = FakeClient({"/database/search": {"results": []}})
    assert discogs.price_wish(client, "A", "B", None, None) is None


def test_service_delegates_to_module_functions(monkeypatch):
    client = FakeClient({"/oauth/identity": {"username": "fan"}})
    service = discogs.DiscogsService(client, has_token=True)
    assert service.identity() == "fan"
    client = FakeClient({"/oauth/identity": {"username": "bad name!"}})
    with pytest.raises(ValueError):
        discogs.DiscogsService(client, True).identity()


# --- Command-line entry points ---------------------------------------------------------------


def test_credentials_prompts_and_prints(monkeypatch):
    answers = iter(["a long enough password", "a long enough password"])
    monkeypatch.setattr(credentials.getpass, "getpass", lambda prompt: next(answers))
    monkeypatch.setattr(credentials.sys, "argv", ["credentials"])
    out = io.StringIO()
    monkeypatch.setattr(credentials.sys, "stdout", out)
    assert credentials.main() == 0
    text = out.getvalue()
    assert "MYVINYL_SECRET_KEY=" in text and "MYVINYL_PASSWORD_HASH=$argon2id$" in text
    key = text.split("MYVINYL_TOKEN_KEY=")[1].strip()
    assert len(tokens.parse_key(key)) == 32
    assert "a long enough password" not in text


@pytest.mark.parametrize(
    "answers", [["short", "short"], ["a long enough password", "a different password!"]]
)
def test_credentials_rejects_bad_passwords(monkeypatch, answers):
    it = iter(answers)
    monkeypatch.setattr(credentials.getpass, "getpass", lambda prompt: next(it))
    monkeypatch.setattr(credentials.sys, "argv", ["credentials"])
    monkeypatch.setattr(credentials.sys, "stderr", io.StringIO())
    assert credentials.main() == 1


def test_access_log_redacts_link_tokens():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s"',
        ("1.2.3.4", "GET", "/link/s3cr3t?x=1"),
        None,
    )
    assert entry.RedactLinkTokens().filter(record) is True
    assert record.args == ("1.2.3.4", "GET", "/link/[redacted]?x=1")
    plain = logging.LogRecord("x", logging.INFO, "", 0, "no args", None, None)
    assert entry.RedactLinkTokens().filter(plain) is True


def test_main_starts_uvicorn_on_localhost(monkeypatch):
    seen = {}
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, **kw: seen.update(app=app, **kw))
    monkeypatch.setattr(
        entry.sys if hasattr(entry, "sys") else __import__("sys"), "argv", ["myvinyl"]
    )
    entry.main()
    assert seen["app"] == "myvinyl.main:app_from_env" and seen["factory"] is True
    assert seen["host"] == "127.0.0.1" and seen["server_header"] is False
