"""Parallel lanes: pure helpers, no bot state.

Ported from the WolfBet reference bot (wolfbet.py, CLI v2.35.0 to v2.36.0).
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
# Lane ids a registry may hold; at most `lanes.max` (default LANES_MAX) are enabled at once.
LANE_ID_MAX = 99

LANE_NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,32}$")
LANE_NAME_HELP = "lane name must be 1-32 characters: letters, digits, space, _ or -"

# What a lane may be asked to do when its current session ends.
DRAIN_ACTIONS = ("restart", "remove")

# Stop-reason prefix when a lane's next bet is larger than its equity.
# Not on the recurring restart whitelist, so it never restarts.
LANE_BUDGET_REASON = "Lane budget exceeded"

# `config-set laneK.key=<one of these>` removes the override.
LANE_UNSET_VALUES = ("default", "unset", "inherit")

# v2.36.0: how lanes share the money (`lanes.split`, shared by all lanes).
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


def lane_file_ids(path: str) -> List[int]:
    """Lane ids that have a file next to `path` (lane 1 = `path`, lane K = its .laneK twin)."""
    import glob
    root, ext = os.path.splitext(path)
    ids = [1] if os.path.exists(path) else []
    for f in glob.glob(f"{glob.escape(root)}.lane*{glob.escape(ext)}"):
        tag = f[len(root) + len(".lane"):len(f) - len(ext)]
        if tag.isdigit():
            ids.append(int(tag))
    return sorted(ids)


def default_lane_name(lane: int) -> str:
    return f"Lane {lane}"


def lane_registry(cfg: dict) -> dict:
    """Every configured lane as {id: {"name", "enabled", "overrides"}}, ids 1..LANE_ID_MAX.

    Reads `lanes.items`; a config written before lanes had names (`lanes.count` plus
    `lanes.overrides`) reads as lanes 1..count enabled, and lanes that only have overrides
    above the count as disabled, so both shapes behave the same. Lane 1 is always enabled.
    """
    block = _lanes_block(cfg)
    items = block.get("items")
    reg = {}
    if isinstance(items, dict):
        for key, entry in items.items():
            try:
                lane = int(key)
            except (TypeError, ValueError):
                continue
            if not 1 <= lane <= LANE_ID_MAX or not isinstance(entry, dict):
                continue
            name = entry.get("name")
            reg[lane] = {
                "name": name if isinstance(name, str) and LANE_NAME_RE.match(name)
                else default_lane_name(lane),
                "enabled": bool(entry.get("enabled", True)),
                "overrides": dict(entry.get("overrides") or {}),
            }
    else:
        try:
            count = max(1, min(LANES_MAX, int(block.get("count") or 1)))
        except (TypeError, ValueError):
            count = 1
        overrides = block.get("overrides")
        overrides = overrides if isinstance(overrides, dict) else {}
        ids = set(range(1, count + 1))
        for key in overrides:
            if str(key).isdigit() and 1 <= int(key) <= LANES_MAX:
                ids.add(int(key))
        for lane in sorted(ids):
            ov = overrides.get(str(lane))
            reg[lane] = {"name": default_lane_name(lane), "enabled": lane <= count,
                         "overrides": dict(ov) if isinstance(ov, dict) else {}}
    reg.setdefault(1, {"name": default_lane_name(1), "enabled": True, "overrides": {}})
    reg[1]["enabled"] = True
    return reg


def lanes_max(cfg: dict) -> int:
    """How many lanes may be enabled at once: the owner-set `lanes.max`, default LANES_MAX."""
    try:
        return max(1, min(LANE_ID_MAX, int(_lanes_block(cfg).get("max") or LANES_MAX)))
    except (TypeError, ValueError):
        return LANES_MAX


def lane_ids(cfg: dict) -> List[int]:
    """Every registry lane id, enabled or not, in order."""
    return sorted(lane_registry(cfg))


def lane_name(cfg: dict, lane: int) -> str:
    entry = lane_registry(cfg).get(lane)
    return entry["name"] if entry else default_lane_name(lane)


def split_registry_pairs(pairs: List[str]) -> Tuple[List[str], dict, dict]:
    """Split config-set pairs for a bot with a lane registry into (base, {lane: [pairs]}, block).

    `block` holds the lanes-wide settings given: "count" (lanes=N), "split" (lane_split) and
    "max" (lanes_max). The count is checked against the owner limit by the caller. Pairs
    without "=" go to base so the bot's own parser reports them.
    """
    base, per_lane, block = [], {}, {}
    for pair in pairs:
        if "=" not in pair:
            base.append(pair)
            continue
        key, val = pair.split("=", 1)
        key = key.strip().lower()
        m = re.match(r"^lane(\d+)\.(.+)$", key)
        if key in ("lanes", "lanes_max"):
            try:
                n = int(val.strip())
            except ValueError:
                raise ValueError(f"{key} must be an integer 1-{LANE_ID_MAX}, got {val!r}")
            if not 1 <= n <= LANE_ID_MAX:
                raise ValueError(f"{key} must be 1-{LANE_ID_MAX}, got {n}")
            block["count" if key == "lanes" else "max"] = n
        elif key == "lane_split":
            split = val.strip().lower()
            if split not in LANE_SPLIT_MODES:
                raise ValueError(f"lane_split must be one of {', '.join(LANE_SPLIT_MODES)}, "
                                 f"got {val!r}")
            block["split"] = split
        elif m and m.group(2) == "name":
            lane, name = int(m.group(1)), val.strip().strip('"').strip("'").strip()
            if not 1 <= lane <= LANE_ID_MAX:
                raise ValueError(f"lanes are 1-{LANE_ID_MAX}, got lane{lane}")
            if not LANE_NAME_RE.match(name):
                raise ValueError(LANE_NAME_HELP)
            per_lane.setdefault(lane, []).append(f"name={name}")
        elif m:
            lane = int(m.group(1))
            if not 2 <= lane <= LANE_ID_MAX:
                raise ValueError(f"lane overrides are for lanes 2-{LANE_ID_MAX} "
                                 f"(lane 1 uses the base config), got lane{lane}")
            if m.group(2) == "lane_split":
                raise ValueError("lane_split can't differ per lane (shared by all lanes)")
            per_lane.setdefault(lane, []).append(f"{m.group(2)}={val}")
        else:
            base.append(pair)
    return base, per_lane, block


def next_lane_id(cfg: dict, reg: dict) -> int:
    """The id the next new lane gets: never one a removed lane had."""
    try:
        stored = int(_lanes_block(cfg).get("next_id") or 0)
    except (TypeError, ValueError):
        stored = 0
    return max(stored, max(reg) + 1)


def set_enabled_count(reg: dict, count: int, next_id: int) -> int:
    """Enable `count` lanes in `reg`: disabled lanes come back first (lowest id), then new ids.

    Lowering the count disables the highest enabled ids; lane 1 always stays. Returns the
    next id. Raises ValueError when the ids run out.
    """
    enabled = [lane for lane in sorted(reg) if reg[lane]["enabled"]]
    for lane in enabled[count:]:
        reg[lane]["enabled"] = False
    need = count - len(enabled)
    for lane in sorted(reg):
        if need > 0 and not reg[lane]["enabled"]:
            reg[lane]["enabled"] = True
            need -= 1
    while need > 0:
        if next_id > LANE_ID_MAX:
            raise ValueError(f"lane ids run out at {LANE_ID_MAX}: removed ids are never reused")
        reg[next_id] = {"name": default_lane_name(next_id), "enabled": True, "overrides": {}}
        next_id += 1
        need -= 1
    return next_id


def registry_block(cfg: dict, reg: dict, next_id: int, split: Optional[str] = None,
                   lanes_max: Optional[int] = None) -> dict:
    """The `lanes` block to write: split, the owner limit when set, next_id and the items."""
    block = {"split": split if split is not None else lane_split(cfg)}
    limit = lanes_max if lanes_max is not None else _lanes_block(cfg).get("max")
    if limit is not None:
        block["max"] = limit
    block["next_id"] = next_id
    block["items"] = {str(lane): reg[lane] for lane in sorted(reg)}
    return block


def request_drain(path: str, lane: int, action: str) -> None:
    """Ask lane `lane` to `restart` or be `remove`d when its session ends; `cancel` forgets it.

    `path` is the lane-1 request file; lane K uses its .laneK twin, written 0600.
    """
    target = lane_path(path, lane)
    if action == "cancel":
        if os.path.exists(target):
            os.remove(target)
        return
    if action not in DRAIN_ACTIONS:
        raise ValueError(f"drain action must be one of {DRAIN_ACTIONS} or cancel, got {action!r}")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(action)


def take_drain(path: str, lane: int) -> Optional[str]:
    """The request waiting for lane `lane`, deleted before it is returned; None when there is none.

    Deleting first means a lane that fails to come back never loops on its request. An
    unreadable file raises OSError for the caller to log.
    """
    target = lane_path(path, lane)
    if not os.path.exists(target):
        return None
    with open(target) as f:
        action = f.read().strip()
    os.remove(target)
    return action


def validate_lane(lane: Optional[int]) -> int:
    """`--lane` value → lane number (None = 1); outside 1..LANES_MAX raises."""
    lane = 1 if lane is None else int(lane)
    if not 1 <= lane <= LANES_MAX:
        raise ValueError(f"--lane must be 1-{LANES_MAX}, got {lane}")
    return lane


def lanes_count(cfg: dict) -> int:
    """How many lanes run: the enabled lanes of the registry (`lanes.count`, clamped to
    1..LANES_MAX, for a config without named lanes)."""
    return sum(1 for entry in lane_registry(cfg).values() if entry["enabled"])


def lane_split(cfg: dict) -> str:
    """Configured split mode; anything unknown is "none"."""
    mode = str(_lanes_block(cfg).get("split") or "none").strip().lower()
    return mode if mode in LANE_SPLIT_MODES else "none"


def lane_overrides(cfg: dict, lane: int, forbidden: Iterable[str]) -> dict:
    """Per-lane override dict without forbidden keys (lane 1 has none)."""
    if lane <= 1:
        return {}
    ov = (lane_registry(cfg).get(lane) or {}).get("overrides") or {}
    forbidden = set(forbidden)
    return {k: v for k, v in ov.items() if k not in forbidden}


def split_budget_on(lanes: int, mode: str) -> bool:
    """True when each lane may only risk balance ÷ lanes."""
    return lanes > 1 and mode in ("budget", "both")


def split_bet_divisor(lanes: int, mode: str) -> int:
    """Lane count when the bet split applies, else 1 (base bet / basebet_pct)."""
    return lanes if lanes > 1 and mode in ("bet", "both") else 1


def lane_config(cfg: dict, lane: int, forbidden: Iterable[str],
                min_bet: float = 0) -> Tuple[dict, Optional[str]]:
    """Base config + this lane's overrides + the bet split (v2.36.0).

    A lane with its own base_bet override keeps that exact value: the user
    already sized it. A split bet that would round to 0 or fall below the
    service's min_bet (0 = no minimum) stays full, and the second value is a
    warning for the caller to log (else None).
    """
    ov = lane_overrides(cfg, lane, forbidden)
    merged = {**cfg, **ov}
    if "base_bet" in merged:
        merged["base_bet"], note = split_base_bet(
            merged["base_bet"], lanes_count(cfg), lane_split(cfg), "base_bet" in ov,
            min_bet)
        return merged, note
    return merged, None


def split_base_bet(base, lanes: int, mode: str, has_override: bool,
                   min_bet: float = 0) -> Tuple[object, Optional[str]]:
    """This lane's base bet under the bet split (v2.36.0) → (bet, note|None).

    A lane's own base_bet override is never divided, and one lane (or a mode
    without the bet split) is a no-op. A split bet that rounds to 0 or falls
    below the service's minimum bet stays full; the note says why, for the
    caller to log. Also used for a bet derived from basebet_pct.
    """
    n = split_bet_divisor(lanes, mode)
    if has_override or n <= 1 or not isinstance(base, (int, float)):
        return base, None
    split = round(base / n, 8)
    if split <= 0:
        return base, f"lane split: base_bet {base:.8f} ÷ {n} rounds to 0: using full bet"
    if min_bet and split < min_bet:
        return base, (f"lane split: base_bet {base:.8f} ÷ {n} = {split:.8f} is below "
                      f"the minimum bet {min_bet:.8f}: using full bet")
    return split, None


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
