"""Tests for shared/core/run_increment.py.

Pure cases ported from wolfbet/tests/test_phase10b_run_increment.py (CLI
v2.37.0): scope parsing, off values, run file per lane, DB sum, stale run,
resume, and the merged milestone check without WolfBet's auto-UUID restart.
"""

import json
import os
import sqlite3
import stat

import pytest

from shared.core.database import SESSIONS_SCHEMA
from shared.core.run_increment import (
    OFF_VALUES, PROFIT_INCREMENT_SCOPES, check_profit_increment, db_max_session_id,
    increment_on, load_run, next_milestone, parse_scope, resume_base_bet,
    run_is_stale, run_path, run_profit_done, save_run,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(SESSIONS_SCHEMA)
    yield c
    c.close()


def _seed(conn, rows):
    """rows: [(id, profit, lane)]; lane None = an old row without the tag."""
    for sid, profit, lane in rows:
        conn.execute("INSERT INTO sessions (id, profit, lane) VALUES (?, ?, ?)",
                     (sid, profit, lane))
    conn.commit()


class TestConstants:
    def test_values(self):
        assert PROFIT_INCREMENT_SCOPES == ("session", "run")
        assert OFF_VALUES == ("none", "off", "0", "0.0")


class TestParseScope:
    @pytest.mark.parametrize("value,expected", [("session", "session"), ("run", "run"),
                                                (" Run ", "run"), ("SESSION", "session")])
    def test_valid(self, value, expected):
        assert parse_scope(value) == expected

    @pytest.mark.parametrize("value", ["lifetime", "", None])
    def test_invalid(self, value):
        with pytest.raises(ValueError, match="profit_increment_scope must be session or run"):
            parse_scope(value)


class TestIncrementOn:
    def test_both_set(self):
        assert increment_on(0.0001, 0.01)
        assert increment_on("0.0001", "0.01")

    @pytest.mark.parametrize("pi,pt", [
        (None, 0.01), (0.0001, None), (0, 0.01), (0.0001, 0.0), (-1, 0.01),
        ("none", 0.01), ("OFF", 0.01), (0.0001, "0"), (0.0001, "0.0"), ("", 0.01),
        ("abc", 0.01), (0.0001, "-0.5"),
    ])
    def test_off(self, pi, pt):
        assert not increment_on(pi, pt)


class TestRunFile:
    def test_path_per_lane(self):
        assert run_path("/h/.x_run.json") == "/h/.x_run.json"
        assert run_path("/h/.x_run.json", 1) == "/h/.x_run.json"
        assert run_path("/h/.x_run.json", 3) == "/h/.x_run.lane3.json"

    def test_load_missing(self, tmp_path):
        assert load_run(str(tmp_path / "nope.json")) is None

    @pytest.mark.parametrize("content", ["{not json", "[]", '{"bumps": 1}',
                                         '{"first_session": "x"}', "null"])
    def test_load_corrupt(self, tmp_path, content):
        p = tmp_path / "run.json"
        p.write_text(content)
        assert load_run(str(p)) is None

    def test_load_normalises(self, tmp_path):
        p = tmp_path / "run.json"
        p.write_text(json.dumps({"first_session": "5", "started_at": "t", "bumps": -2}))
        assert load_run(str(p)) == {"first_session": 5, "started_at": "t", "bumps": 0}
        p.write_text(json.dumps({"first_session": 5}))
        assert load_run(str(p)) == {"first_session": 5, "started_at": None, "bumps": 0}

    def test_save_round_trip_mode_0600(self, tmp_path):
        p = str(tmp_path / "run.json")
        run = {"first_session": 12, "started_at": "2026-11-15T10:00:00", "bumps": 2}
        save_run(p, run)
        assert load_run(p) == run
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
        assert not os.path.exists(p + ".tmp")
        save_run(p, {**run, "bumps": 3})            # replace keeps the mode
        assert load_run(p)["bumps"] == 3
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600

    def test_save_failure_raises_and_cleans_tmp(self, tmp_path):
        p = str(tmp_path / "missing_dir" / "run.json")
        with pytest.raises(OSError):
            save_run(p, {"first_session": 1})
        assert not os.path.exists(p + ".tmp")


class TestDb:
    def test_sum_since_first_with_lane(self, conn):
        # WolfBet test_run_profit_sums_sessions_and_bumps_persist
        _seed(conn, [(1, 0.5, 1), (5, 0.004, 1), (6, 0.003, 1), (7, 0.9, 2)])
        assert run_profit_done(conn, 5, lane=1) == pytest.approx(0.007)
        assert run_profit_done(conn, 5, lane=2) == pytest.approx(0.9)
        assert run_profit_done(conn, 5) == pytest.approx(0.907)

    def test_exclude_current_session(self, conn):
        _seed(conn, [(5, 0.004, 1), (6, 0.003, 1)])
        assert run_profit_done(conn, 5, exclude_sid=6, lane=1) == pytest.approx(0.004)
        assert run_profit_done(conn, 5, exclude_sid=6) == pytest.approx(0.004)

    def test_untagged_rows_count_as_lane_1(self, conn):
        _seed(conn, [(2, 0.01, None), (3, 0.02, 2)])
        assert run_profit_done(conn, 2, lane=1) == pytest.approx(0.01)
        assert run_profit_done(conn, 2, lane=2) == pytest.approx(0.02)

    def test_empty_and_error(self, conn):
        assert run_profit_done(conn, 1) == 0.0
        bad = sqlite3.connect(":memory:")            # no sessions table
        assert run_profit_done(bad, 1, lane=1) == 0.0
        assert db_max_session_id(bad) == 0
        bad.close()

    def test_max_session_id(self, conn):
        assert db_max_session_id(conn) == 0
        _seed(conn, [(3, 0.0, 1), (9, 0.0, 2)])
        assert db_max_session_id(conn) == 9
        assert db_max_session_id(conn, lane=1) == 3
        assert db_max_session_id(conn, lane=4) == 0


class TestRunState:
    def test_stale(self):
        # WolfBet test_stale_run_file_after_db_reset_starts_new_run
        assert run_is_stale({"first_session": 5000}, 1)
        assert not run_is_stale({"first_session": 2}, 1)   # next session to start
        assert run_is_stale({"first_session": 3}, 1)
        assert not run_is_stale({"first_session": 1}, 9)

    def test_resume_base_bet(self):
        # WolfBet test_resume_restores_bumps_on_base_bet
        assert resume_base_bet(0.0001, 2, 0.0001) == pytest.approx(0.0003)
        assert resume_base_bet(0.0001, 0, 0.0001) == 0.0001
        assert resume_base_bet(0.0001, 2, None) == 0.0001
        assert resume_base_bet(0.0001, 2, 0.0) == 0.0001
        assert resume_base_bet(0.1, 3, 0.1) == 0.4             # rounded to 8 places

    def test_next_milestone(self):
        assert next_milestone(0, 0.01) == pytest.approx(0.01)
        assert next_milestone(2, 0.01) == pytest.approx(0.03)


class TestCheckProfitIncrement:
    def test_below_milestone(self):
        assert check_profit_increment(0.005, 0.01, 0.01, 0.0001, 0.0001, 0) == \
            (False, 0.0001, 0.01, 0)

    def test_session_scope_steps(self):
        # WolfBet test_session_scope_uses_session_profit
        bumped, base, ms, _ = check_profit_increment(0.01, 0.01, 0.01, 0.0001, 0.0001, 0)
        assert bumped and base == pytest.approx(0.0002) and ms == pytest.approx(0.02)

    def test_run_total_bump(self):
        # WolfBet test_run_profit_sums_sessions_and_bumps_persist: 0.007 done + 0.003
        done = 0.007
        assert not check_profit_increment(done + 0.002, 0.01, 0.01, 0.0001, 0.0001, 0)[0]
        assert check_profit_increment(done + 0.003, 0.01, 0.01, 0.0001, 0.0001, 0) == \
            (True, pytest.approx(0.0002), pytest.approx(0.02), 1)

    def test_big_jump_past_two_milestones_bumps_once_per_check(self):
        bumped, base, ms, bumps = check_profit_increment(0.025, 0.01, 0.01, 0.0001, 0.0001, 0)
        assert bumped and base == pytest.approx(0.0002) and ms == pytest.approx(0.02)
        assert bumps == 1
        bumped, base, ms, bumps = check_profit_increment(0.025, ms, 0.01, 0.0001, base, bumps)
        assert bumped and base == pytest.approx(0.0003) and ms == pytest.approx(0.03)
        assert bumps == 2
        assert not check_profit_increment(0.025, ms, 0.01, 0.0001, base, bumps)[0]

    @pytest.mark.parametrize("inc,thr", [(0.0, 0.01), (None, 0.01), (0.0001, None),
                                         ("none", 0.01), (0.0001, "0")])
    def test_off_never_bumps(self, inc, thr):
        # WolfBet test_zero_increment_never_bumps_or_counts
        assert check_profit_increment(1.0, 0.01, thr, inc, 0.0001, 0) == \
            (False, 0.0001, 0.01, 0)

    def test_string_amounts(self):
        assert check_profit_increment(0.01, 0.01, "0.01", "0.0001", 0.0001, 0)[1] == \
            pytest.approx(0.0002)
