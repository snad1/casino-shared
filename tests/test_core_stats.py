"""Tests for shared/core/stats.py.

Cases ported from wolfbet/tests: test_phase9c_stats_window_avgs.py (window
part), test_phase6d_reset_and_datestats.py (date-window helper) and
test_phase9d_durations.py (fmt_duration and the time-span helper).
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from shared.core.database import SESSIONS_SCHEMA
from shared.core.stats import (
    fmt_duration, normalize_window_bound, sessions_time_span, stats_date_window,
)


def _db(tmp_path, rows, lanes=None):
    """sqlite sessions table seeded with (started_at, ended_at) rows."""
    conn = sqlite3.connect(str(tmp_path / "stats.db"))
    conn.execute(SESSIONS_SCHEMA)
    if lanes is not None:
        conn.execute("ALTER TABLE sessions ADD COLUMN lane INTEGER DEFAULT 1")
    for i, (started, ended) in enumerate(rows):
        conn.execute("INSERT INTO sessions (started_at, ended_at) VALUES (?, ?)",
                     (started, ended))
        if lanes is not None and lanes[i] is not None:
            conn.execute("UPDATE sessions SET lane = ? WHERE id = last_insert_rowid()",
                         (lanes[i],))
    conn.commit()
    return conn


# ── window bound normalisation ────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("2026-11-13", "2026-11-13"),
    ("2026-11-13 14:00", "2026-11-13T14:00"),
    ("2026-11-13 14:00:05", "2026-11-13T14:00:05"),
    ("  2026-11-13 14:00:05.5 ", "2026-11-13T14:00:05.5"),
    ("2026-11-13T14:00", "2026-11-13T14:00"),
])
def test_normalize_bound(raw, expected):
    assert normalize_window_bound(raw) == expected


@pytest.mark.parametrize("raw", ["yesterday", "2026-13-01", "14:00", "2026-11-13 25:00"])
def test_normalize_bound_rejects_garbage(raw):
    with pytest.raises(ValueError, match=r'Invalid --since/--until ".*": use YYYY-MM-DD'):
        normalize_window_bound(raw)


@pytest.mark.parametrize("raw", [None, ""])
def test_normalize_bound_empty_is_none(raw):
    assert normalize_window_bound(raw) is None


# ── date-window clause ────────────────────────────────────
def test_window_params_use_t_form():
    _, params, label = stats_date_window(since="2026-11-13 14:00",
                                         until="2026-11-13 15:30:10")
    assert params == ["2026-11-13T14:00", "2026-11-13T15:30:10"]
    assert "2026-11-13 14:00" in label


def test_date_window_empty_when_no_args():
    assert stats_date_window() == ("", [], "")


def test_date_window_since_only():
    clause, params, label = stats_date_window(since="2026-08-01")
    assert clause == " WHERE started_at >= ?"
    assert params == ["2026-08-01"]
    assert "2026-08-01 → …" in label


def test_date_window_bare_until_extends_end_of_day():
    _, params, _ = stats_date_window(until="2026-08-15")
    assert params == ["2026-08-15T23:59:59.999999"]


def test_date_window_full_ts_until_untouched():
    _, params, _ = stats_date_window(until="2026-08-15T06:00:00")
    assert params == ["2026-08-15T06:00:00"]


def test_date_window_invalid_raises():
    with pytest.raises(ValueError):
        stats_date_window(since="yesterday")


def test_space_bound_excludes_earlier_same_day(tmp_path):
    conn = _db(tmp_path, [("2026-11-13T13:00:00", None),
                          ("2026-11-13T14:30:00", None)])
    where, params, _ = stats_date_window(since="2026-11-13 14:00")
    got = [r[0] for r in conn.execute(f"SELECT started_at FROM sessions{where}", params)]
    assert got == ["2026-11-13T14:30:00"]


def test_bare_until_includes_whole_day(tmp_path):
    conn = _db(tmp_path, [("2026-08-15T20:00:00", None),
                          ("2026-08-16T00:00:00", None)])
    where, params, _ = stats_date_window(until="2026-08-15")
    got = [r[0] for r in conn.execute(f"SELECT started_at FROM sessions{where}", params)]
    assert got == ["2026-08-15T20:00:00"]


def test_lane_clause_and_label():
    clause, params, label = stats_date_window(since="2026-11-13", lane=2)
    assert clause == " WHERE started_at >= ? AND COALESCE(lane, 1) = ?"
    assert params == ["2026-11-13", 2]
    assert label.endswith("  \\[lane 2]")


def test_lane_filter_counts_null_as_lane_one(tmp_path):
    conn = _db(tmp_path, [("2026-11-13T10:00:00", None)] * 3, lanes=[None, 1, 2])
    conn.execute("UPDATE sessions SET lane = NULL WHERE id = 1")
    for lane, n in ((1, 2), (2, 1), (3, 0)):
        where, params, _ = stats_date_window(lane=lane)
        assert conn.execute(f"SELECT COUNT(*) FROM sessions{where}", params).fetchone()[0] == n


# ── fmt_duration ──────────────────────────────────────────
@pytest.mark.parametrize("sec,expected", [
    (0, "0s"),
    (45, "45s"),
    (62, "1m 2s"),
    (180, "3m 0s"),
    (3600, "1h 0m 0s"),
    (86405, "1d 0h 0m 5s"),
    (50 * 86400 + 7383, "1mo 20d 2h 3m 3s"),
    (None, "0s"),
    (-5, "0s"),
])
def test_fmt_duration(sec, expected):
    assert fmt_duration(sec) == expected


# ── sessions_time_span ────────────────────────────────────
ROWS = [
    ("2026-11-13T10:00:00.000000", "2026-11-13T10:00:30.000000"),  # 30s
    ("2026-11-13T11:00:00.000000", "2026-11-13T11:01:00.000000"),  # 60s
    ("2026-11-13T12:00:00.000000", "2026-11-13T12:02:05.000000"),  # 125s
]


def test_span_helper(tmp_path):
    first, last, span, bet = sessions_time_span(_db(tmp_path, ROWS))
    assert first == datetime(2026, 11, 13, 10, 0, 0)
    assert last == datetime(2026, 11, 13, 12, 2, 5)
    assert span == 2 * 3600 + 125
    assert bet == 215


def test_span_helper_window_and_running(tmp_path):
    started = (datetime.now() - timedelta(seconds=100)).isoformat()
    conn = _db(tmp_path, ROWS + [(started, None)])
    where, params, _ = stats_date_window(since="2026-11-13 11:00")
    first, last, span, bet = sessions_time_span(conn, where, params)
    assert first == datetime(2026, 11, 13, 11, 0, 0)
    assert 285 - 1 <= bet <= 285 + 5      # 60 + 125 + ~100 running
    assert last >= datetime.now() - timedelta(seconds=5)


def test_span_helper_empty(tmp_path):
    assert sessions_time_span(_db(tmp_path, [])) == (None, None, 0, 0)


def test_span_helper_tz_aware_row_to_local(tmp_path):
    t0 = datetime(2026, 11, 13, 10, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=90)
    first, last, span, bet = sessions_time_span(
        _db(tmp_path, [(t0.isoformat(), t1.isoformat())]))
    assert first == t0.astimezone().replace(tzinfo=None)
    assert first.tzinfo is None
    assert span == bet == 90


def test_span_helper_skips_unparseable_rows(tmp_path):
    conn = _db(tmp_path, ROWS[:1] + [("garbage", None), (None, None)])
    first, last, span, bet = sessions_time_span(conn)
    assert first == datetime(2026, 11, 13, 10, 0, 0)
    assert span == bet == 30
