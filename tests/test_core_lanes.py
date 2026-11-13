"""Tests for shared/core/lanes.py and the sessions.lane column.

Pure cases ported from wolfbet/tests/test_phase10_lanes.py (CLI v2.35.0 and
the v2.36.0 lane_split cases): paths and identity, override load, config-set
parsing and errors, budget stop, share init, bet split, lane recommendation
and the DB lane tag.
"""

import sqlite3

import pytest

from shared.core.database import SESSIONS_SCHEMA, ensure_sessions_lane
from shared.core.lanes import (
    LANE_BUDGET_REASON, LANE_SPLIT_HELP, LANE_SPLIT_MODES, LANE_UNSET_VALUES,
    LANES_MAX, check_lane_keys, lane_budget_exceeded, lane_config, lane_equity,
    lane_overrides, lane_path, lane_share, lane_split, lanes_count,
    recommend_lanes, split_bet_divisor, split_budget_on, split_lane_pairs,
    validate_lane,
)
from shared.core.stats import stats_date_window

FORBIDDEN = {"api_key", "currency", "game", "proxy", "purge_days", "db_write_mode"}


def _cfg(lanes=None, base_bet=0.0001, **extra):
    cfg = {"api_key": "k", "currency": "usdt", "base_bet": base_bet, "max_wins": 5, **extra}
    if lanes is not None:
        cfg["lanes"] = lanes
    return cfg


class TestConstants:
    def test_values(self):
        assert LANES_MAX == 10
        assert LANE_BUDGET_REASON == "Lane budget exceeded"
        assert LANE_UNSET_VALUES == ("default", "unset", "inherit")
        assert LANE_SPLIT_MODES == ("none", "budget", "bet", "both")
        assert set(LANE_SPLIT_HELP) == set(LANE_SPLIT_MODES)


class TestPaths:
    def test_lane_one_unchanged(self):
        assert lane_path("/h/.x_live.json", 1) == "/h/.x_live.json"

    def test_lane_k_inserts_suffix(self):
        assert lane_path("/h/.x_live.json", 3) == "/h/.x_live.lane3.json"
        assert lane_path("/h/.x.pid", 2) == "/h/.x.lane2.pid"

    def test_validate_lane(self):
        assert validate_lane(None) == 1
        assert validate_lane(1) == 1
        assert validate_lane("10") == 10

    @pytest.mark.parametrize("bad", [0, 11, -1])
    def test_validate_lane_range(self, bad):
        with pytest.raises(ValueError, match=r"--lane must be 1-10"):
            validate_lane(bad)


class TestCountAndSplit:
    @pytest.mark.parametrize("cfg,expected", [
        ({}, 1),
        ({"lanes": None}, 1),
        ({"lanes": "3"}, 1),               # malformed block
        ({"lanes": {"count": "x"}}, 1),
        ({"lanes": {"count": 0}}, 1),
        ({"lanes": {"count": 3}}, 3),
        ({"lanes": {"count": "4"}}, 4),
        ({"lanes": {"count": 99}}, LANES_MAX),
    ])
    def test_lanes_count(self, cfg, expected):
        assert lanes_count(cfg) == expected

    @pytest.mark.parametrize("cfg,expected", [
        ({}, "none"),
        ({"lanes": {"count": 2}}, "none"),
        ({"lanes": {"split": " Both "}}, "both"),
        ({"lanes": {"split": "half"}}, "none"),
        ({"lanes": {"split": "budget"}}, "budget"),
    ])
    def test_lane_split(self, cfg, expected):
        assert lane_split(cfg) == expected

    def test_budget_and_bet_flags(self):
        assert not split_budget_on(1, "both")
        assert not split_budget_on(3, "none") and not split_budget_on(3, "bet")
        assert split_budget_on(3, "budget") and split_budget_on(3, "both")
        assert split_bet_divisor(1, "both") == 1
        assert split_bet_divisor(3, "budget") == 1 and split_bet_divisor(3, "none") == 1
        assert split_bet_divisor(3, "bet") == 3 and split_bet_divisor(3, "both") == 3


class TestOverrides:
    def test_lane_one_has_none(self):
        cfg = _cfg({"count": 2, "overrides": {"1": {"max_wins": 1}}})
        assert lane_overrides(cfg, 1, FORBIDDEN) == {}

    def test_lane_k_hit_and_forbidden_dropped(self):
        cfg = _cfg({"count": 3, "overrides": {"2": {"max_wins": 3, "currency": "btc"}}})
        assert lane_overrides(cfg, 2, FORBIDDEN) == {"max_wins": 3}
        assert lane_overrides(cfg, 3, FORBIDDEN) == {}

    @pytest.mark.parametrize("block", [None, {"overrides": None}, {"overrides": []},
                                       {"overrides": {"2": "x"}}])
    def test_malformed_tolerated(self, block):
        assert lane_overrides(_cfg(block), 2, FORBIDDEN) == {}


class TestLaneConfig:
    def test_overrides_merged_forbidden_kept_from_base(self):
        cfg = _cfg({"count": 2, "overrides": {"2": {"max_wins": 3, "currency": "btc"}}})
        merged, msg = lane_config(cfg, 2, FORBIDDEN)
        assert merged["max_wins"] == 3 and merged["currency"] == "usdt" and msg is None
        assert lane_config(cfg, 1, FORBIDDEN)[0]["max_wins"] == 5

    def test_default_none_uses_full_bet(self):
        assert lane_config(_cfg({"count": 2}), 2, FORBIDDEN)[0]["base_bet"] == 0.0001

    @pytest.mark.parametrize("mode,expected", [("none", 0.0001), ("budget", 0.0001),
                                               ("bet", 0.00005), ("both", 0.00005),
                                               ("half", 0.0001)])
    def test_split_modes_base_bet(self, mode, expected):
        for lane in (1, 2):
            merged, _ = lane_config(_cfg({"count": 2, "split": mode}), lane, FORBIDDEN)
            assert merged["base_bet"] == pytest.approx(expected)

    def test_one_lane_no_op(self):
        assert lane_config(_cfg({"count": 1, "split": "both"}), 1, FORBIDDEN)[0]["base_bet"] == 0.0001

    def test_base_bet_override_never_divided(self):
        cfg = _cfg({"count": 2, "split": "bet", "overrides": {"2": {"base_bet": 0.0003}}})
        assert lane_config(cfg, 2, FORBIDDEN)[0]["base_bet"] == pytest.approx(0.0003)
        assert lane_config(cfg, 1, FORBIDDEN)[0]["base_bet"] == pytest.approx(0.00005)

    def test_rounding_to_zero_keeps_full(self):
        cfg = _cfg({"count": 3, "split": "bet"}, base_bet=0.00000001)
        merged, msg = lane_config(cfg, 2, FORBIDDEN)
        assert merged["base_bet"] == 0.00000001
        assert msg and "rounds to 0" in msg and "÷ 3" in msg

    def test_does_not_mutate_input(self):
        cfg = _cfg({"count": 2, "split": "bet"})
        lane_config(cfg, 2, FORBIDDEN)
        assert cfg["base_bet"] == 0.0001


class TestSplitLanePairs:
    def test_split(self):
        base, per_lane, count, split = split_lane_pairs(
            ["max_wins=4", "lanes=3", "lane2.base_bet=0.001", "LANE3.maxwins=1", "lane_split=Both"])
        assert base == ["max_wins=4"] and count == 3 and split == "both"
        assert per_lane == {2: ["base_bet=0.001"], 3: ["maxwins=1"]}

    def test_no_lane_keys(self):
        assert split_lane_pairs(["max_wins=4", "junk"]) == (["max_wins=4", "junk"], {}, None, None)

    def test_unset_value_passes_through(self):
        _, per_lane, _, _ = split_lane_pairs(["lane2.max_wins=default"])
        assert per_lane == {2: ["max_wins=default"]}

    @pytest.mark.parametrize("pairs,msg", [
        (["lanes=0"], "lanes must be 1-10"),
        (["lanes=11"], "lanes must be 1-10"),
        (["lanes=x"], "lanes must be an integer 1-10"),
        (["lane1.base_bet=1"], "lane 1 uses the base config"),
        (["lane11.base_bet=1"], "got lane11"),
        (["lane_split=half"], "lane_split must be one of none, budget, bet, both"),
        (["lane2.lane_split=bet"], "lane_split can't differ per lane"),
    ])
    def test_rejects(self, pairs, msg):
        with pytest.raises(ValueError, match=msg):
            split_lane_pairs(pairs)


class TestCheckLaneKeys:
    def test_forbidden_refused(self):
        with pytest.raises(ValueError, match=r"^currency can't differ per lane \(shared by all lanes\)$"):
            check_lane_keys(["max_wins", "currency"], FORBIDDEN)

    def test_allowed_passes(self):
        assert check_lane_keys(["max_wins", "base_bet"], FORBIDDEN) is None


class TestBudget:
    def test_share_by_mode(self):
        for mode in ("none", "bet"):
            assert lane_share(1.0, 4, mode) is None
        for mode in ("budget", "both"):
            assert lane_share(1.0, 4, mode) == pytest.approx(0.25)

    def test_share_one_lane_or_no_balance(self):
        assert lane_share(1.0, 1, "budget") is None
        assert lane_share(0.0, 4, "budget") is None

    def test_equity(self):
        assert lane_equity(None, 0.5, 0.5) is None
        assert lane_equity(0.01, 0.002, -0.005) == pytest.approx(0.007)

    def test_stop_when_next_bet_exceeds_equity(self):
        stop, reason = lane_budget_exceeded(0.008, lane_equity(0.01, 0.002, -0.005))
        assert stop and reason.startswith(LANE_BUDGET_REASON)
        assert reason == "Lane budget exceeded (next bet 0.00800000, lane equity 0.00700000)"

    def test_ok_within_equity(self):
        assert lane_budget_exceeded(0.004, lane_equity(0.01, 0.0, -0.005)) == (False, "")

    def test_no_budget_without_split(self):
        assert lane_budget_exceeded(99.0, None) == (False, "")


class TestRecommendLanes:
    def test_wolfbet_table(self):
        rows = {2: {"rps": 50, "s429": 0}, 3: {"rps": 70, "s429": 0}, 4: {"rps": 75, "s429": 0}}
        assert recommend_lanes(28, rows) == 3
        assert recommend_lanes(28, {2: {"rps": 50, "s429": 3}}) == 1
        assert recommend_lanes(28, {2: {"rps": 30, "s429": 0}}) == 1
        assert recommend_lanes(28, {}) == 1


class TestSessionsLane:
    _OLD = ("CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "started_at TEXT, ended_at TEXT)")

    def _cols(self, conn):
        return [r[1] for r in conn.execute("PRAGMA table_info(sessions)")]

    def test_fresh_schema_has_lane(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(SESSIONS_SCHEMA)
        assert "lane" in self._cols(conn)
        conn.execute("INSERT INTO sessions (started_at) VALUES ('2026-11-13T10:00:00')")
        assert conn.execute("SELECT lane FROM sessions").fetchone()[0] == 1
        ensure_sessions_lane(conn)                     # already there: no-op
        assert self._cols(conn).count("lane") == 1

    def test_old_table_migrated_idempotently(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(self._OLD)
        conn.execute("INSERT INTO sessions (started_at, ended_at) "
                     "VALUES ('2026-11-13T10:00:00', '2026-11-13T10:00:10')")
        ensure_sessions_lane(conn)
        ensure_sessions_lane(conn)
        assert self._cols(conn).count("lane") == 1
        assert conn.execute("SELECT lane FROM sessions").fetchone()[0] == 1
        conn.execute("INSERT INTO sessions (started_at, ended_at, lane) "
                     "VALUES ('2026-11-13T11:00:00', '2026-11-13T11:00:10', 2)")
        for lane, ids in ((1, [1]), (2, [2])):
            clause, params, _ = stats_date_window(None, None, lane=lane)
            got = [r[0] for r in conn.execute(
                f"SELECT id FROM sessions {clause} ORDER BY id", params)]
            assert got == ids

    def test_null_lane_counts_as_lane_one(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(SESSIONS_SCHEMA)
        conn.execute("INSERT INTO sessions (started_at, lane) VALUES ('2026-11-13T10:00:00', NULL)")
        clause, params, _ = stats_date_window(None, None, lane=1)
        assert conn.execute(f"SELECT COUNT(*) FROM sessions {clause}", params).fetchone()[0] == 1
