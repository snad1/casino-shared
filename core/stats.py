"""Stats window and duration helpers — pure functions over the sessions table.

Ported from the WolfBet reference bot (wolfbet.py, CLI v2.30.9–v2.35.0).
Each service builds its own stats / analytics output and calls these for the
--since/--until window, the lane filter and the time-span figures, so the
logic exists once for every casino. Column names follow SESSIONS_SCHEMA.
"""

from datetime import datetime
from typing import List, Optional, Tuple


def normalize_window_bound(value) -> Optional[str]:
    """Turn a --since/--until value into the stored ISO form (WolfBet v2.33.0).

    started_at is stored as datetime.isoformat() ("2026-11-13T11:35:20.75").
    A bound typed with a space ("2026-11-13 14:00") compared wrong as a string
    (' ' sorts before 'T'), so every session that day matched. Swap the first
    space for 'T' and validate. Raises ValueError on anything unparseable.
    """
    if not value:
        return None
    v = str(value).strip().replace(" ", "T", 1)
    try:
        datetime.fromisoformat(v)
    except ValueError:
        raise ValueError(
            f'Invalid --since/--until "{value}": use YYYY-MM-DD[ HH:MM[:SS]]') from None
    return v


def stats_date_window(since: Optional[str] = None, until: Optional[str] = None,
                      lane: Optional[int] = None) -> Tuple[str, List, str]:
    """Build an optional window clause for the sessions table (WolfBet v2.30.9).

    since/until accept bare dates (YYYY-MM-DD) or ISO timestamps; a bare until
    date is extended to end of day so the day is inclusive. ISO strings compare
    correctly as text in SQLite. lane filters on sessions.lane, with rows from
    before lanes existed counted as lane 1 (v2.35.0).

    Returns (clause, params, label): clause is "" or " WHERE …", label
    describes the window for headers (Rich markup-safe).
    """
    since = normalize_window_bound(since)
    until = normalize_window_bound(until)
    conds, params = [], []
    if since:
        conds.append("started_at >= ?")
        params.append(since)
    if until:
        u = until if len(until) > 10 else until + "T23:59:59.999999"
        conds.append("started_at <= ?")
        params.append(u)
    if lane:
        conds.append("COALESCE(lane, 1) = ?")
        params.append(int(lane))
    if not conds:
        return "", [], ""
    clause = " WHERE " + " AND ".join(conds)
    label = ""
    if since or until:
        label = (f"  [{(since or '…').replace('T', ' ')} → "
                 f"{(until or '…').replace('T', ' ')}]")
    if lane:
        label += f"  \\[lane {int(lane)}]"   # escaped: Rich would eat [lane N] as a tag
    return clause, params, label


def fmt_duration(sec) -> str:
    """Format seconds as "1mo 20d 2h 3m 2s" (WolfBet v2.34.0).

    Months are 30 days. Leading zero units are dropped; seconds are always
    shown. None and negative values read as 0.
    """
    sec = max(0, int(sec or 0))
    parts = []
    for name, size in (("mo", 30 * 86400), ("d", 86400), ("h", 3600), ("m", 60)):
        q, sec = divmod(sec, size)
        if q or parts:
            parts.append(f"{q}{name}")
    parts.append(f"{sec}s")
    return " ".join(parts)


def sessions_time_span(conn, where: str = "", params=()):
    """Return (first_start, last_end, span_sec, bet_sec) for sessions in a window.

    span = first started_at to last ended_at (a running session ends "now").
    bet  = sum of per-session (ended_at - started_at): pauses between sessions
    are excluded. Rows with an unparseable started_at are skipped. Returns
    (None, None, 0, 0) when no row is usable. Pass the clause and params from
    stats_date_window as where / params. (WolfBet v2.34.0)
    """
    def _local(ts):
        t = datetime.fromisoformat(ts)
        # tz-aware rows -> local naive, so they compare with the naive local
        # timestamps the bots write.
        return t.astimezone().replace(tzinfo=None) if t.tzinfo else t

    now = datetime.now()
    first = last = None
    bet = 0.0
    for started, ended in conn.execute(
            f"SELECT started_at, ended_at FROM sessions{where}", tuple(params)):
        try:
            t0 = _local(started)
            t1 = _local(ended) if ended else now
        except (TypeError, ValueError):
            continue
        bet += max(0.0, (t1 - t0).total_seconds())
        first = t0 if first is None or t0 < first else first
        last = t1 if last is None or t1 > last else last
    if first is None:
        return None, None, 0, 0
    return first, last, max(0.0, (last - first).total_seconds()), bet
