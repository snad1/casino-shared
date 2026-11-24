"""The lane registry, read from named lanes or the older count shape, and session-end requests."""

import os
import stat

import pytest

from shared.core.lanes import (
    LANE_ID_MAX, LANE_NAME_HELP, LANES_MAX, default_lane_name, lane_file_ids, lane_ids,
    lane_name, lane_overrides, lane_path, lane_registry, lanes_count, lanes_max, next_lane_id,
    registry_block, request_drain, set_enabled_count, split_registry_pairs, take_drain,
)

FORBIDDEN = {"api_key", "currency"}


def test_named_lanes_keep_their_names_flags_and_overrides():
    cfg = {"lanes": {"items": {
        "1": {"name": "main", "enabled": False},
        "2": {"name": "friend-A", "overrides": {"multiplier": 3.0, "api_key": "x"}},
        "3": {"name": "bad/name!", "enabled": False},
        "100": {"name": "too-high"},
        "x": {"name": "not-a-lane"},
        "4": "not-a-dict",
    }}}
    reg = lane_registry(cfg)
    assert sorted(reg) == [1, 2, 3]
    assert reg[1] == {"name": "main", "enabled": True, "overrides": {}}
    assert reg[2]["name"] == "friend-A" and reg[2]["enabled"] is True
    assert reg[3] == {"name": "Lane 3", "enabled": False, "overrides": {}}
    assert lanes_count(cfg) == 2
    assert lane_overrides(cfg, 2, FORBIDDEN) == {"multiplier": 3.0}
    assert lane_ids(cfg) == [1, 2, 3]
    assert (lane_name(cfg, 2), lane_name(cfg, 9)) == ("friend-A", default_lane_name(9))


def test_the_count_shape_reads_as_it_always_did():
    cfg = {"lanes": {"count": 2, "overrides": {"2": {"multiplier": 3.0}, "5": {"rule": "under"}}}}
    reg = lane_registry(cfg)
    assert {k: v["enabled"] for k, v in reg.items()} == {1: True, 2: True, 5: False}
    assert lanes_count(cfg) == 2
    assert lane_overrides(cfg, 2, FORBIDDEN) == {"multiplier": 3.0}
    assert lane_overrides(cfg, 1, FORBIDDEN) == {}


@pytest.mark.parametrize(("lanes", "count"), [
    (None, 1), ({"count": 0}, 1), ({"count": "x"}, 1), ({"count": 50}, LANES_MAX), ("bad", 1),
])
def test_a_missing_or_bad_count_is_clamped(lanes, count):
    cfg = {} if lanes is None else {"lanes": lanes}
    assert lanes_count(cfg) == count
    assert lane_registry(cfg)[1]["enabled"] is True


@pytest.mark.parametrize(("value", "cap"), [(None, LANES_MAX), (4, 4), (500, LANE_ID_MAX), ("x", LANES_MAX)])
def test_the_enabled_cap(value, cap):
    assert lanes_max({"lanes": {"max": value}}) == cap


def test_lane_files_are_found_next_to_the_lane_one_file(tmp_path):
    base = str(tmp_path / "live.json")
    assert lane_file_ids(base) == []
    for lane in (1, 3, 12):
        open(lane_path(base, lane), "w").close()
    open(str(tmp_path / "live.laneX.json"), "w").close()
    assert lane_file_ids(base) == [1, 3, 12]


def test_a_request_is_private_read_once_and_cancellable(tmp_path):
    path = str(tmp_path / "drain")
    assert take_drain(path, 2) is None
    request_drain(path, 2, "restart")
    assert stat.S_IMODE(os.stat(lane_path(path, 2)).st_mode) == 0o600
    assert take_drain(path, 1) is None
    assert take_drain(path, 2) == "restart"
    assert take_drain(path, 2) is None
    request_drain(path, 2, "remove")
    request_drain(path, 2, "cancel")
    request_drain(path, 2, "cancel")
    assert not os.path.exists(lane_path(path, 2))


def test_an_unknown_request_is_refused(tmp_path):
    with pytest.raises(ValueError):
        request_drain(str(tmp_path / "drain"), 2, "explode")
    assert not os.path.exists(lane_path(str(tmp_path / "drain"), 2))


def test_registry_pairs_split_into_base_lanes_and_lane_wide_settings():
    base, per_lane, block = split_registry_pairs([
        "maxloss=5", "lanes=12", "lanes_max=20", "lane_split=BET", "lane1.name='main'",
        "lane15.multiplier=3", "lane15.name=friend-A", "noequals"])
    assert base == ["maxloss=5", "noequals"]
    assert block == {"count": 12, "max": 20, "split": "bet"}
    assert per_lane == {1: ["name=main"], 15: ["multiplier=3", "name=friend-A"]}


@pytest.mark.parametrize(("pair", "message"), [
    ("lanes=0", f"lanes must be 1-{LANE_ID_MAX}, got 0"),
    ("lanes_max=x", f"lanes_max must be an integer 1-{LANE_ID_MAX}, got 'x'"),
    ("lane_split=half", "lane_split must be one of none, budget, bet, both, got 'half'"),
    ("lane1.multiplier=3", "lane overrides are for lanes 2-99 (lane 1 uses the base config), got lane1"),
    ("lane100.name=x", "lanes are 1-99, got lane100"),
    ("lane2.name=bad/name", LANE_NAME_HELP),
    ("lane2.lane_split=bet", "lane_split can't differ per lane (shared by all lanes)"),
])
def test_bad_registry_pairs_are_refused(pair, message):
    with pytest.raises(ValueError) as exc:
        split_registry_pairs([pair])
    assert str(exc.value) == message


def _reg(*enabled, off=()):
    return {k: {"name": default_lane_name(k), "enabled": k not in off, "overrides": {}}
            for k in sorted(set(enabled) | set(off))}


def test_disabled_lanes_come_back_first_then_new_ids():
    reg = _reg(1, 2, off=(3,))
    assert set_enabled_count(reg, 4, 5) == 6
    assert {k: v["enabled"] for k, v in reg.items()} == {1: True, 2: True, 3: True, 5: True}


def test_lowering_the_count_disables_the_highest_ids_and_keeps_lane_one():
    reg = _reg(1, 2, 3)
    assert set_enabled_count(reg, 1, 4) == 4
    assert [k for k, v in reg.items() if v["enabled"]] == [1]


def test_ids_run_out_at_the_ceiling():
    with pytest.raises(ValueError, match="lane ids run out at 99"):
        set_enabled_count(_reg(1), 2, LANE_ID_MAX + 1)


def test_a_removed_id_is_never_given_again():
    reg = _reg(1, 2)
    assert next_lane_id({"lanes": {"next_id": 7}}, reg) == 7
    assert next_lane_id({}, reg) == 3


def test_the_written_block_keeps_split_and_limit():
    cfg = {"lanes": {"split": "budget", "max": 12}}
    block = registry_block(cfg, _reg(1, 3), 4)
    assert block == {"split": "budget", "max": 12, "next_id": 4,
                     "items": {"1": _reg(1)[1], "3": _reg(3)[3]}}
    assert registry_block({}, _reg(1), 2, split="bet", lanes_max=5)["max"] == 5
    assert "max" not in registry_block({}, _reg(1), 2)
    assert lane_registry({"lanes": block}) == _reg(1, 3)
