"""Run-wide profit increment — pure helpers, no bot state.

Ported from the WolfBet reference bot (wolfbet.py, CLI v2.37.0).
`profit_increment` raises the base bet each time profit reaches the next
`profit_threshold` milestone. Scope "session" measures the current session's
profit (the milestone resets each recurring cycle, as before). Scope "run"
measures the total since the run started: the run is kept in a small JSON
file per lane ({first_session, started_at, bumps}) so the bumps survive
restarts, and the finished sessions' profit comes from the stats DB.

Each service keeps its own state object, file paths and logging and calls
these with plain values. WolfBet's auto-bet UUID restart on a bump is N/A:
no target has a server-side auto-bet, so the bumped base applies to the
next bet.
"""

import json
import os
import sqlite3
from typing import Optional, Tuple

from .lanes import lane_path

PROFIT_INCREMENT_SCOPES = ("session", "run")

# `profit_increment` / `profit_threshold` values that mean off (as do None,
# "" and any number <= 0).
OFF_VALUES = ("none", "off", "0", "0.0")


def parse_scope(value) -> str:
    """'Run ' → 'run'; anything but session / run raises (WolfBet's text)."""
    v = str(value or "").strip().lower()
    if v not in PROFIT_INCREMENT_SCOPES:
        raise ValueError(f"profit_increment_scope must be session or run, got {v!r}")
    return v


def _amount(v) -> Optional[float]:
    """Positive increment / threshold value, or None when it means off."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = v.strip()
        if not v or v.lower() in OFF_VALUES:
            return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def increment_on(pi, pt) -> bool:
    """True only when both profit_increment and profit_threshold are set and > 0."""
    return _amount(pi) is not None and _amount(pt) is not None


def run_path(base_path: str, lane: int = 1) -> str:
    """'~/.x_run.json' + lane 2 → '~/.x_run.lane2.json'. Lane 1 is unchanged."""
    return lane_path(base_path, lane)


def load_run(path: str) -> Optional[dict]:
    """The saved run, or None when the file is missing or corrupt."""
    try:
        with open(path) as f:
            d = json.load(f)
        return {"first_session": int(d["first_session"]),
                "started_at": d.get("started_at"),
                "bumps": max(0, int(d.get("bumps") or 0))}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_run(path: str, run: dict) -> None:
    """Write the run atomically (tmp file + os.replace), mode 0o600.

    Raises OSError on failure (the tmp file is removed); the caller logs it.
    """
    tmp = path + ".tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"first_session": run["first_session"],
                       "started_at": run.get("started_at"),
                       "bumps": run.get("bumps", 0)}, f)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _lane_clause(lane: Optional[int]) -> Tuple[str, tuple]:
    if lane is None:
        return "", ()
    return " AND COALESCE(lane,1) = ?", (int(lane),)


def run_profit_done(conn, first_sid: int, exclude_sid: Optional[int] = None,
                    lane: Optional[int] = None) -> float:
    """Sum of session profits since `first_sid`, without `exclude_sid`.

    `lane` limits the sum to that lane's sessions (older rows count as lane
    1). A DB error reads as 0.0, as in WolfBet.
    """
    clause, args = _lane_clause(lane)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(profit),0) FROM sessions WHERE id >= ? AND id != ?" + clause,
            (first_sid, exclude_sid if exclude_sid is not None else -1, *args)).fetchone()
        return float(row[0] or 0.0)
    except sqlite3.Error:
        return 0.0


def db_max_session_id(conn, lane: Optional[int] = None) -> int:
    """Highest session id (optionally of one lane); 0 when empty or on error."""
    clause, args = _lane_clause(lane)
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(id),0) FROM sessions WHERE 1=1" + clause, args).fetchone()
        return int(row[0] or 0)
    except sqlite3.Error:
        return 0


def run_is_stale(run: dict, max_sid: int) -> bool:
    """True when the run starts past the DB's next session id (stats were reset)."""
    return run["first_session"] > max_sid + 1


def resume_base_bet(config_base: float, bumps: int, increment) -> float:
    """Base bet on resume: the configured base plus the bumps already made."""
    inc = _amount(increment)
    if inc and bumps:
        return round(config_base + bumps * inc, 8)
    return config_base


def next_milestone(bumps: int, threshold: float) -> float:
    """Profit needed for the next bump after `bumps` bumps."""
    return (bumps + 1) * threshold


def check_profit_increment(profit: float, next_ms: float, threshold, increment,
                           base: float, bumps: int) -> Tuple[bool, float, float, int]:
    """Bump the base bet when profit reaches the next milestone.

    → (bumped, base, next_ms, bumps). One bump per call, like WolfBet: a
    profit jump past two milestones bumps once now and once on the next
    check. `profit` is the session profit (scope session) or the run total
    (scope run); in session scope the caller ignores `bumps`.
    """
    inc, thr = _amount(increment), _amount(threshold)
    if inc is None or thr is None or profit < next_ms:
        return False, base, next_ms, bumps
    return True, base + inc, next_ms + thr, bumps + 1
