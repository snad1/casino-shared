"""Parallel lanes — pure helpers, no bot state.

Ported from the WolfBet reference bot (wolfbet.py, CLI v2.35.0–v2.36.0).
A lane is one bot process with its own session, Martingale chain, counters,
stops and pauses. Lane 1 is the classic single bot (paths unchanged); lanes
2..N run side by side on the same account, config file and stats DB.

Each service keeps its own state object and its own set of keys that can't
differ per lane (credentials, currency, game, proxy, ...) and passes both in
as plain values, so the logic exists once for every casino.
"""

import os
import re
from typing import Iterable, List, Optional, Tuple

LANES_MAX = 10

# Stop-reason prefix when a lane's next bet is larger than its equity.
# Not on the recurring restart whitelist, so it never restarts.
LANE_BUDGET_REASON = "Lane budget exceeded"

# `config-set laneK.key=<one of these>` removes the override.
LANE_UNSET_VALUES = ("default", "unset", "inherit")

# v2.36.0 — how lanes share the money (`lanes.split`, shared by all lanes).
#   none   → every lane uses the full base bet, no lane budget (default)
#   budget → each lane may risk balance ÷ lanes
#   bet    → base bet (and basebet_pct) ÷ lanes
#   both   → budget + bet
LANE_SPLIT_MODES = ("none", "budget", "bet", "both")
LANE_SPLIT_HELP = {
    "none":   "every lane uses the full base bet, no lane budget",
    "budget": "each lane may risk balance ÷ lanes",
    "bet":    "base bet ÷ lanes",
    "both":   "base bet ÷ lanes and each lane may risk balance ÷ lanes",
}


def _lanes_block(cfg: dict) -> dict:
    block = cfg.get("lanes") if isinstance(cfg, dict) else None
    return block if isinstance(block, dict) else {}


def lane_path(path: str, lane: int) -> str:
    """'~/.x_live.json' + lane 2 → '~/.x_live.lane2.json'. Lane 1 is unchanged."""
    if lane <= 1:
        return path
    root, ext = os.path.splitext(path)
    return f"{root}.lane{lane}{ext}"


def validate_lane(lane: Optional[int]) -> int:
    """`--lane` value → lane number (None = 1); outside 1..LANES_MAX raises."""
    lane = 1 if lane is None else int(lane)
    if not 1 <= lane <= LANES_MAX:
        raise ValueError(f"--lane must be 1-{LANES_MAX}, got {lane}")
    return lane


def lanes_count(cfg: dict) -> int:
    """Configured lane count, clamped to 1..LANES_MAX; a bad block counts as 1."""
    try:
        n = int(_lanes_block(cfg).get("count") or 1)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(LANES_MAX, n))


def lane_split(cfg: dict) -> str:
    """Configured split mode; anything unknown is "none"."""
    mode = str(_lanes_block(cfg).get("split") or "none").strip().lower()
    return mode if mode in LANE_SPLIT_MODES else "none"


def lane_overrides(cfg: dict, lane: int, forbidden: Iterable[str]) -> dict:
    """Per-lane override dict without forbidden keys (lane 1 has none)."""
    if lane <= 1:
        return {}
    overrides = _lanes_block(cfg).get("overrides")
    ov = overrides.get(str(lane)) if isinstance(overrides, dict) else None
    if not isinstance(ov, dict):
        return {}
    forbidden = set(forbidden)
    return {k: v for k, v in ov.items() if k not in forbidden}


def split_budget_on(lanes: int, mode: str) -> bool:
    """True when each lane may only risk balance ÷ lanes."""
    return lanes > 1 and mode in ("budget", "both")


def split_bet_divisor(lanes: int, mode: str) -> int:
    """Lane count when the bet split applies, else 1 (base bet / basebet_pct)."""
    return lanes if lanes > 1 and mode in ("bet", "both") else 1


def lane_config(cfg: dict, lane: int,
                forbidden: Iterable[str]) -> Tuple[dict, Optional[str]]:
    """Base config + this lane's overrides + the bet split (v2.36.0).

    A lane with its own base_bet override keeps that exact value: the user
    already sized it. A split bet that would round to 0 stays full, and the
    second value is a warning for the caller to log (else None).
    """
    ov = lane_overrides(cfg, lane, forbidden)
    merged = {**cfg, **ov}
    n = lanes_count(cfg)
    if (split_bet_divisor(n, lane_split(cfg)) > 1 and "base_bet" not in ov
            and isinstance(merged.get("base_bet"), (int, float))):
        split = round(merged["base_bet"] / n, 8)
        if split > 0:
            merged["base_bet"] = split
        else:
            return merged, (f"lane split: base_bet {merged['base_bet']:.8f} ÷ {n} "
                            f"rounds to 0 — using full bet")
    return merged, None


def split_lane_pairs(pairs: List[str]) -> Tuple[List[str], dict, Optional[int], Optional[str]]:
    """Split config-set pairs into (base, {lane: [pairs]}, lanes_count|None, split|None).

    Handles "lanes=N", "lane_split=MODE" and "laneK.key=value". Pairs without
    "=" go to base so the service's own parser reports them.
    """
    base, per_lane, count, split = [], {}, None, None
    for pair in pairs:
        if "=" not in pair:
            base.append(pair)
            continue
        key, val = pair.split("=", 1)
        key = key.strip().lower()
        m = re.match(r"^lane(\d+)\.(.+)$", key)
        if key == "lanes":
            try:
                count = int(val.strip())
            except ValueError:
                raise ValueError(f"lanes must be an integer 1-{LANES_MAX}, got {val!r}")
            if not 1 <= count <= LANES_MAX:
                raise ValueError(f"lanes must be 1-{LANES_MAX}, got {count}")
        elif key == "lane_split":
            split = val.strip().lower()
            if split not in LANE_SPLIT_MODES:
                raise ValueError(f"lane_split must be one of {', '.join(LANE_SPLIT_MODES)}, "
                                 f"got {val!r}")
        elif m:
            lane = int(m.group(1))
            if not 2 <= lane <= LANES_MAX:
                raise ValueError(f"lane overrides are for lanes 2-{LANES_MAX} "
                                 f"(lane 1 uses the base config), got lane{lane}")
            if m.group(2) == "lane_split":
                raise ValueError("lane_split can't differ per lane (shared by all lanes)")
            per_lane.setdefault(lane, []).append(f"{m.group(2)}={val}")
        else:
            base.append(pair)
    return base, per_lane, count, split


def check_lane_keys(keys: Iterable[str], forbidden: Iterable[str]) -> None:
    """Raise ValueError naming the first key that can't differ per lane."""
    forbidden = set(forbidden)
    for k in keys:
        if k in forbidden:
            raise ValueError(f"{k} can't differ per lane (shared by all lanes)")


def lane_share(balance: float, lanes: int, mode: str) -> Optional[float]:
    """This lane's share of the balance at bot start; None = no budget guard."""
    if split_budget_on(lanes, mode) and balance > 0:
        return balance / lanes
    return None


def lane_equity(share: Optional[float], run_profit: float,
                profit: float) -> Optional[float]:
    """Money the lane may still risk: share + its result so far. None = no split."""
    if share is None:
        return None
    return share + run_profit + profit


def lane_budget_exceeded(next_bet: float,
                         equity: Optional[float]) -> Tuple[bool, str]:
    """(True, stop reason) when the next bet is larger than the lane equity."""
    if equity is not None and next_bet > equity:
        return True, (f"{LANE_BUDGET_REASON} (next bet {next_bet:.8f}, "
                      f"lane equity {equity:.8f})")
    return False, ""


def recommend_lanes(base_rps: float, rows: dict) -> int:
    """Highest lane count whose throughput still gains ≥ 30% per added lane
    over the previous count, with no 429s. rows = {lanes: {"rps", "s429"}}."""
    best, prev = 1, base_rps
    for k in sorted(rows):
        r = rows[k]
        if r["s429"] or r["rps"] < prev * 1.3:
            break
        best, prev = k, r["rps"]
    return best
