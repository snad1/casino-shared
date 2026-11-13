"""Benchmark diagnostics — pure functions, no bot state.

Ported from the WolfBet reference bot (core/diagnostics.py, CLI v2.28.0).
No I/O, no engine state. Callers render the colour hint as they prefer
(Rich markup for a CLI, Markdown for TG). The casino name is a parameter
so each service names itself in the verdict text.
"""

from typing import Iterable, Tuple


def percentile(sorted_ms, pct: float) -> float:
    """Percentile from a PRE-SORTED sequence of ms values. pct in [0.0, 1.0].

    Returns 0.0 for an empty sequence. Uses a simple index formula
    (int(pct * n), clamped to the last index), matching WolfBet byte-for-byte.
    """
    if not sorted_ms:
        return 0.0
    idx = max(0, min(len(sorted_ms) - 1, int(pct * len(sorted_ms))))
    return sorted_ms[idx]


def benchmark_verdict(*, p50: float, p99: float, statuses: Iterable,
                      sample_count: int, casino_name: str) -> Tuple[str, str]:
    """Return (colour_hint, plain_english_message) for benchmark output.

    Colour hints: "red" (rate-limited), "yellow" (network bottleneck /
    tail-latency), "green" (network fast), "cyan" (network OK).

    Tier order (WolfBet v2.26.1):
      1. Any 429     -> rate-limited
      2. p50 >= 100ms -> network bottleneck
      3. p99 > 3*p50 -> tail-latency spikes
      4. p50 < 30ms  -> network fast
      5. otherwise   -> network OK
    """
    got_429 = sum(1 for s in statuses if s == 429)
    if got_429:
        return ("red",
                f"Rate-limited ({got_429}/{sample_count} got 429). "
                f"{casino_name} is throttling this IP. Slow down or use a proxy.")
    if p50 >= 100:
        max_bps = 1000.0 / p50 if p50 > 0 else 0.0
        return ("yellow",
                f"Network bottleneck (p50={p50:.0f}ms → ceiling ~{max_bps:.1f} BPS). "
                f"{casino_name} response is the limit.")
    if p50 > 0 and p99 > 3 * p50:
        return ("yellow",
                f"Tail-latency spikes (p99={p99:.0f}ms, {p99/p50:.1f}× p50). "
                "Unstable network path.")
    if p50 < 30:
        return ("green",
                f"Network fast (p50={p50:.0f}ms). If live BPS is lower, "
                "the bottleneck is elsewhere — try --diagnose.")
    max_bps = 1000.0 / p50 if p50 > 0 else 0.0
    return ("cyan",
            f"Network OK (p50={p50:.0f}ms → ceiling ~{max_bps:.1f} BPS).")
