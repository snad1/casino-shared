"""Bot event publisher: ships bet, state and lifecycle events to Redis Streams.

Each bot builds one publisher with its own stream prefix and bet field order,
so the dashboard reads ``<prefix>:bets``, ``<prefix>:state`` and
``<prefix>:lifecycle`` for that bot.

The betting thread only calls the emit_* methods, which build one tuple and
append it to a bounded deque. A daemon thread drains the deque every 100 ms
and sends each batch as one pipelined XADD. Redis being slow, down or missing
never reaches the betting thread: failed batches are dropped and counted, and
when the queue is full new events are dropped and counted, without building
them (see reserve). Every event carries a per-process seq, and a dropped
event still uses up its seq, so a consumer sees any loss as a gap.

Queued events are flat frames of plain values (kind, seq, ts_ms, fields...)
rather than tuples: a bet then leaves no garbage-collected object alive on the
queue, so a busy queue never makes the collector pause the betting thread.
The sender also gives up the interpreter lock every few events while it
encodes, so a betting thread never waits behind a whole batch.

Wire format (schema v1): each stream entry has one field, ``data``, holding
compact JSON ``{"v", "svc", "lane", "pid", "seq", "ts_ms", "type", "payload"}``.

A scoped publisher serves many users from one process and one sender thread
(a Telegram bot): every event names its user, goes to
``<prefix>:<uid>:<stream>`` with ``uid`` in the payload, and seq counts per
user, so a gap in one user's stream still means that user lost events.
"""

import collections
import itertools
import json
import logging
import os
import threading
import time

SCHEMA_VERSION = 1

LIFECYCLE_KINDS = (
    "start", "stop", "pause", "resume", "reload", "cycle",
    "session_open", "session_close", "error",
)

_STREAMS = {"bet": "bets", "state": "state", "lifecycle": "lifecycle"}
_DEFAULT_MAXLEN = {"bets": 200_000, "state": 20_000, "lifecycle": 20_000}

_TICK_S = 0.1
_BATCH_MAX = 500
_YIELD_EVERY = 32
_BACKOFF_MIN_S = 0.5
_BACKOFF_MAX_S = 30.0
_LOG_EVERY_S = 60.0
_CONNECT_TIMEOUT_S = 0.1
_REPLY_TIMEOUT_S = 1.0

EVENTS_URL_SCHEMES = ("redis", "rediss", "unix")

logger = logging.getLogger("shared.events")


def validate_events_url(url: str) -> str:
    """Accept redis://, rediss:// or unix:// URLs with a host or socket path; raise ValueError otherwise."""
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
        ok = parts.scheme in EVENTS_URL_SCHEMES and (
            parts.path if parts.scheme == "unix" else parts.hostname)
        if ok and parts.port is not None and not 0 < parts.port < 65536:
            ok = False
    except ValueError:
        ok = False
    if not ok:
        raise ValueError("events_url must be redis://host[:port], rediss://host[:port] "
                         "or unix:///path/to/socket")
    return url


class EventPublisher:
    """Bounded, non-blocking event queue with its own Redis sender thread.

    ``stream_prefix`` names the bot's streams and ``bet_fields`` the order of
    the values passed to emit_bet; ``svc`` (default: the prefix) goes into
    every envelope. ``client`` replaces the Redis client built from ``url``;
    tests pass a fake. ``scoped`` makes every emit take the user it belongs
    to (``uid=``).
    """

    def __init__(self, url, *, stream_prefix, bet_fields, lane=1, svc=None,
                 maxlen=None, queue_max=50_000, client=None, scoped=False):
        self.url = url
        self.lane = lane
        self.svc = svc or stream_prefix
        self.bet_fields = tuple(bet_fields)
        self.pid = os.getpid()
        self._prefix = stream_prefix
        self._maxlen = dict(_DEFAULT_MAXLEN, **(maxlen or {}))
        self._queue = collections.deque()
        self._scoped = scoped
        self._queue_max = queue_max * (3 + len(self.bet_fields) + scoped)
        self._seq = itertools.count()
        self._seqs = {}
        if scoped:
            self._emit = self._emit_scoped
        self._client = client
        self._enabled = False
        self._stop = threading.Event()
        self._thread = None
        self._backoff = 0.0
        self._last_log = 0.0
        self._queued = 0
        self._overflow = 0
        self._sent = 0
        self._dropped = 0
        self._connected = False
        self._last_error = ""

    def reserve(self, uid=None):
        """True when the next event fits in the queue. When full, count it as lost and return False.

        Lets the betting thread skip building an event that would be dropped anyway.
        """
        if not self._enabled:
            return False
        if len(self._queue) < self._queue_max:
            return True
        self._next_seq(uid)
        self._overflow += 1
        return False

    def emit_bet(self, fields, uid=None):
        """Queue one bet; ``fields`` follows the ``bet_fields`` order."""
        if len(fields) != len(self.bet_fields):
            if self._enabled:
                self._next_seq(uid)
                self._dropped += 1
            return
        self._emit("bet", fields, uid)

    def emit_state(self, state, uid=None):
        """Queue one state snapshot dict."""
        self._emit("state", (state,), uid)

    def emit_lifecycle(self, kind, data, uid=None):
        """Queue one lifecycle event; ``kind`` is one of LIFECYCLE_KINDS."""
        self._emit("lifecycle", (kind, data), uid)

    def _next_seq(self, uid):
        if not self._scoped:
            return next(self._seq)
        counter = self._seqs.get(uid)
        if counter is None:
            counter = self._seqs[uid] = itertools.count()
        return next(counter)

    def _emit(self, kind, values, uid=None):
        if not self._enabled:
            return
        try:
            seq = next(self._seq)
            if len(self._queue) >= self._queue_max:
                self._overflow += 1
                return
            self._queue.extend((kind, seq, time.time_ns() // 1_000_000) + values)
            self._queued += 1
        except Exception:
            pass

    def _emit_scoped(self, kind, values, uid=None):
        if not self._enabled:
            return
        try:
            seq = self._next_seq(uid)
            if len(self._queue) >= self._queue_max:
                self._overflow += 1
                return
            self._queue.extend((kind, uid, seq, time.time_ns() // 1_000_000) + values)
            self._queued += 1
        except Exception:
            pass

    def _pop_frame(self):
        """Take one queued event off the front as (kind, seq, ts_ms, data), plus uid when scoped."""
        q = self._queue
        kind = q.popleft()
        uid = q.popleft() if self._scoped else None
        seq, ts_ms = q.popleft(), q.popleft()
        if kind == "bet":
            data = tuple(q.popleft() for _ in self.bet_fields)
        elif kind == "lifecycle":
            data = (q.popleft(), q.popleft())
        else:
            data = q.popleft()
        if self._scoped:
            return kind, seq, ts_ms, data, uid
        return kind, seq, ts_ms, data

    def start(self):
        """Start the sender thread. Returns False when Redis cannot be used."""
        if self._thread is not None and self._thread.is_alive():
            return True
        if self._client is None:
            try:
                import redis
            except ImportError:
                logger.warning("events disabled: the redis package is not installed")
                return False
            self._client = redis.Redis.from_url(
                self.url,
                socket_connect_timeout=_CONNECT_TIMEOUT_S,
                socket_timeout=_REPLY_TIMEOUT_S,
            )
        self._enabled = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="EventPublisher")
        self._thread.start()
        return True

    def stop(self, timeout=2.0):
        """Stop emitting, flush what is queued if Redis answers, and join."""
        self._enabled = False
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def stats(self):
        """Counters for status output."""
        return {
            "queued": self._queued,
            "sent": self._sent,
            "dropped": self._dropped,
            "overflow": self._overflow,
            "last_error": self._last_error,
            "connected": self._connected,
        }

    def encode(self, item):
        """Return (stream key, JSON text) for one queued item."""
        kind, seq, ts_ms, data, *scope = item
        if kind == "bet":
            if len(data) != len(self.bet_fields):
                raise ValueError("bet tuple does not match bet_fields")
            payload = dict(zip(self.bet_fields, data))
        elif kind == "lifecycle":
            payload = {"kind": data[0], "data": data[1]}
        else:
            payload = data
        stream = f"{self._prefix}:{_STREAMS[kind]}"
        if scope:
            uid = scope[0]
            payload = dict(payload, uid=uid)
            stream = f"{self._prefix}:{uid}:{_STREAMS[kind]}"
        event = {
            "v": SCHEMA_VERSION, "svc": self.svc, "lane": self.lane, "pid": self.pid,
            "seq": seq, "ts_ms": ts_ms, "type": kind, "payload": payload,
        }
        text = json.dumps(event, separators=(",", ":"), allow_nan=False)
        return stream, text

    def _run(self):
        while not self._stop.wait(self._backoff or _TICK_S):
            while self._queue and not self._stop.is_set():
                if not self._send_batch():
                    break
        while self._queue and not self._backoff:
            if not self._send_batch():
                break

    def _send_batch(self):
        batch = []
        while self._queue and len(batch) < _BATCH_MAX:
            batch.append(self._pop_frame())
        bad = 0
        try:
            pipe = self._client.pipeline(transaction=False)
            for n, item in enumerate(batch, 1):
                if n % _YIELD_EVERY == 0:
                    time.sleep(0)
                try:
                    stream, text = self.encode(item)
                except (ValueError, TypeError):
                    bad += 1
                    continue
                pipe.xadd(stream, {"data": text},
                          maxlen=self._maxlen[stream.rsplit(":", 1)[1]], approximate=True)
            if bad < len(batch):
                pipe.execute()
        except Exception as exc:
            # After a reply timeout Redis may already hold the batch, so "dropped"
            # means unconfirmed; consumers detect real loss from seq gaps.
            self._dropped += len(batch)
            self._connected = False
            self._last_error = type(exc).__name__
            self._backoff = min(max(self._backoff * 2, _BACKOFF_MIN_S), _BACKOFF_MAX_S)
            now = time.monotonic()
            if now - self._last_log >= _LOG_EVERY_S:
                self._last_log = now
                logger.warning("events: send failed (%s), %d dropped so far, retry in %.1fs",
                               self._last_error, self._dropped, self._backoff)
            return False
        self._dropped += bad
        self._sent += len(batch) - bad
        self._connected = True
        self._backoff = 0.0
        return True
