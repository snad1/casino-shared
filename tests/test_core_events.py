"""Tests for shared/core/events.py: wire format, ordering, loss accounting, and a hot path that never blocks.

Cases ported from the WolfBet publisher tests, run with a bot-neutral prefix
and bet field order so nothing depends on one casino's bet shape.

Run: APP_ENV=testing .venv-test/bin/python -m pytest casino-shared/tests/test_core_events.py -q
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
import types

import pytest

from shared.core.events import EventPublisher, validate_events_url

PREFIX = "demo"
FIELDS = ("session_id", "game", "amount", "multiplier_target", "result_display",
          "state", "profit", "balance_after", "api_ms")
_BET = (168, "limbo", 0.00000002, 2.0, "2.31x", "win", 0.00000002, 2.52702731, 38.4)


class _FakePipe:
    def __init__(self, client):
        self._client = client
        self._ops = []

    def xadd(self, stream, fields, maxlen=None, approximate=False):
        self._ops.append((stream, fields["data"], maxlen, approximate))

    def execute(self):
        self._client.calls += 1
        if self._client.gate is not None:
            self._client.blocked.set()
            self._client.gate.wait(10)
        if self._client.fail:
            raise ConnectionError("redis down")
        self._client.entries.extend(self._ops)


class _FakeRedis:
    def __init__(self, fail=False, gate=None):
        self.fail = fail
        self.gate = gate
        self.blocked = threading.Event()
        self.calls = 0
        self.entries = []

    def pipeline(self, transaction=True):
        assert transaction is False
        return _FakePipe(self)


def _pub(**kw):
    kw.setdefault("stream_prefix", PREFIX)
    kw.setdefault("bet_fields", FIELDS)
    return EventPublisher("redis://unused", **kw)


def _queued_seqs(pub):
    seqs = []
    while pub._queue:
        seqs.append(pub._pop_frame()[1])
    return seqs


def _wait(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def publishers():
    started = []

    def make(client, **kw):
        pub = _pub(client=client, **kw)
        started.append((pub, client))
        return pub
    yield make
    for pub, client in started:
        if client.gate is not None:
            client.gate.set()
        pub.stop(timeout=2.0)


def test_events_arrive_in_order_with_envelope(publishers):
    client = _FakeRedis()
    pub = publishers(client, lane=3)
    assert pub.start()
    for i in range(100):
        pub.emit_bet(_BET)
        pub.emit_state({"total_bets": i})
        pub.emit_lifecycle("cycle", {"n": i})
    pub.stop()

    assert len(client.entries) == 300
    events = [json.loads(data) for _, data, _, _ in client.entries]
    assert [e["seq"] for e in events] == list(range(300))
    assert [s for s, _, _, _ in client.entries[:3]] == [
        "demo:bets", "demo:state", "demo:lifecycle"]
    assert {(s, m, a) for s, _, m, a in client.entries} == {
        ("demo:bets", 200_000, True), ("demo:state", 20_000, True),
        ("demo:lifecycle", 20_000, True)}
    first = events[0]
    assert set(first) == {"v", "svc", "lane", "pid", "seq", "ts_ms", "type", "payload"}
    assert (first["v"], first["svc"], first["lane"], first["pid"]) == (1, "demo", 3, os.getpid())
    assert list(first["payload"]) == list(FIELDS)
    assert first["payload"]["result_display"] == "2.31x"
    assert events[2]["payload"] == {"kind": "cycle", "data": {"n": 0}}
    assert pub.stats()["sent"] == 300 and pub.stats()["connected"] is True


@pytest.mark.parametrize("item, stream, payload", [
    (("bet", 17, 1794945626123, _BET), "demo:bets", dict(zip(FIELDS, _BET))),
    (("state", 18, 1794945626200, {"pid": 4242, "total_bets": 4, "profit": 0.00000005}),
     "demo:state", {"pid": 4242, "total_bets": 4, "profit": 0.00000005}),
    (("lifecycle", 19, 1794945626300, ("session_close", {"session_id": 168, "reason": "Max bets"})),
     "demo:lifecycle", {"kind": "session_close", "data": {"session_id": 168, "reason": "Max bets"}}),
])
def test_encoder_writes_the_v1_envelope(item, stream, payload):
    pub = _pub(lane=2)
    pub.pid = 4242
    got_stream, text = pub.encode(item)
    assert got_stream == stream
    assert json.loads(text) == {"v": 1, "svc": "demo", "lane": 2, "pid": 4242, "seq": item[1],
                                "ts_ms": item[2], "type": item[0], "payload": payload}
    assert " " not in text.replace("Max bets", "")


def test_bet_payload_follows_the_given_field_order():
    fields = ("amount", "session_id")
    pub = _pub(bet_fields=fields)
    _, text = pub.encode(("bet", 0, 1, (0.5, 7)))
    assert list(json.loads(text)["payload"].items()) == [("amount", 0.5), ("session_id", 7)]


def test_svc_can_differ_from_the_stream_prefix():
    pub = _pub(stream_prefix="demo-tg", svc="demo")
    stream, text = pub.encode(("state", 0, 1, {}))
    assert stream == "demo-tg:state" and json.loads(text)["svc"] == "demo"


@pytest.mark.parametrize("missing", ["stream_prefix", "bet_fields"])
def test_prefix_and_bet_fields_are_required(missing):
    kw = {"stream_prefix": PREFIX, "bet_fields": FIELDS}
    del kw[missing]
    with pytest.raises(TypeError):
        EventPublisher("redis://unused", **kw)


def test_scoped_publisher_streams_per_user_with_own_seq(publishers):
    client = _FakeRedis()
    pub = publishers(client, scoped=True)
    pub.start()
    pub.emit_state({"n": 1}, uid=11)
    pub.emit_state({"n": 2}, uid=22)
    pub.emit_bet(_BET, uid=11)
    pub.stop()
    rows = [(s, json.loads(d)) for s, d, _, _ in client.entries]
    assert [s for s, _ in rows] == ["demo:11:state", "demo:22:state", "demo:11:bets"]
    assert [(e["payload"]["uid"], e["seq"]) for _, e in rows] == [(11, 0), (22, 0), (11, 1)]


def test_full_queue_drops_new_events_and_counts(publishers):
    client = _FakeRedis(gate=threading.Event())
    pub = publishers(client, queue_max=10)
    pub.start()
    pub.emit_bet(_BET)
    assert client.blocked.wait(2)
    for _ in range(25):
        pub.emit_bet(_BET)
    assert pub.stats()["overflow"] == 15
    assert pub.stats()["queued"] == 11
    assert _queued_seqs(pub) == list(range(1, 11))


def test_reserve_skips_a_full_queue_but_still_uses_up_the_seq(publishers):
    client = _FakeRedis(gate=threading.Event())
    pub = publishers(client, queue_max=2)
    assert pub.reserve() is False
    pub.start()
    pub.emit_bet(_BET)
    assert client.blocked.wait(2)
    assert pub.reserve() is True
    pub.emit_bet(_BET)
    pub.emit_bet(_BET)
    assert pub.reserve() is False
    assert pub.stats()["overflow"] == 1
    pub.emit_bet(_BET)
    assert _queued_seqs(pub) == [1, 2]
    assert pub.stats()["overflow"] == 2


def test_redis_errors_never_reach_the_caller_and_recover(publishers, caplog):
    client = _FakeRedis(fail=True)
    pub = publishers(client)
    pub.start()
    with caplog.at_level(logging.WARNING, logger="shared.events"):
        for _ in range(5):
            pub.emit_bet(_BET)
        assert _wait(lambda: pub.stats()["dropped"] == 5)
        stats = pub.stats()
        assert stats["connected"] is False
        assert stats["last_error"] == "ConnectionError"
        assert "redis://" not in caplog.text

        client.fail = False
        for _ in range(3):
            pub.emit_bet(_BET)
        assert _wait(lambda: pub.stats()["sent"] == 3)
    assert pub.stats()["connected"] is True
    assert pub._backoff == 0.0
    assert len([r for r in caplog.records if r.name == "shared.events"]) == 1


def test_emit_never_blocks_while_sender_is_stuck(publishers):
    client = _FakeRedis(gate=threading.Event())
    pub = publishers(client)
    pub.start()
    pub.emit_bet(_BET)
    assert client.blocked.wait(2)

    t0 = time.perf_counter()
    for _ in range(100_000):
        pub.emit_bet(_BET)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.2, f"100k emits took {elapsed * 1000:.0f} ms"
    assert pub.stats()["overflow"] == 100_000 - 50_000


def test_bad_event_is_dropped_and_the_rest_of_the_batch_is_sent(publishers):
    client = _FakeRedis()
    pub = publishers(client)
    pub.start()
    nan_bet = _BET[:6] + (float("nan"),) + _BET[7:]
    pub.emit_bet(nan_bet)
    pub.emit_bet(_BET[:5])
    pub.emit_bet(_BET)
    assert _wait(lambda: pub.stats()["sent"] == 1)
    assert pub.stats()["dropped"] == 2
    assert json.loads(client.entries[0][1])["seq"] == 2


def test_emits_before_start_are_ignored():
    pub = _pub(client=_FakeRedis())
    pub.emit_bet(_BET)
    assert pub.stats()["queued"] == 0
    assert len(pub._queue) == 0


def test_missing_redis_package_disables_publisher(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "redis", None)
    pub = _pub()
    with caplog.at_level(logging.WARNING, logger="shared.events"):
        assert pub.start() is False
    assert "redis package is not installed" in caplog.text
    pub.emit_bet(_BET)
    assert pub.stats()["queued"] == 0
    assert not any(t.name == "EventPublisher" for t in threading.enumerate())


def test_connect_gives_up_fast_but_a_late_reply_is_still_awaited(monkeypatch):
    seen = {}

    class Redis:
        @staticmethod
        def from_url(url, **kw):
            seen.update(kw)
            return _FakeRedis()
    monkeypatch.setitem(sys.modules, "redis", types.SimpleNamespace(Redis=Redis))
    pub = _pub()
    try:
        assert pub.start()
    finally:
        pub.stop()
    assert seen == {"socket_connect_timeout": 0.1, "socket_timeout": 1.0}


def test_redis_is_not_imported_unless_started():
    code = (
        "import sys;"
        "from shared.core.events import EventPublisher;"
        "p = EventPublisher('redis://unused', stream_prefix='demo', bet_fields=('a', 'b'));"
        "p.emit_bet((1, 2)); p.stats();"
        "assert 'redis' not in sys.modules, 'redis imported'"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("url", [
    "redis://localhost:6379/0", "rediss://user:pw@redis.example:6380", "unix:///run/redis.sock",
])
def test_events_url_accepts_redis_and_unix(url):
    assert validate_events_url(url) == url


@pytest.mark.parametrize("url", [
    "http://localhost:6379", "redis://", "unix://", "redis://host:70000", "not a url",
])
def test_events_url_rejects_everything_else(url):
    with pytest.raises(ValueError):
        validate_events_url(url)
