"""Tests for shared/core/diagnostics.py.

Cases ported from wolfbet/tests/test_phase5k_core_helpers.py (percentile,
benchmark_verdict) and test_phase5i_diagnostics.py (percentile part).
"""

from shared.core.diagnostics import benchmark_verdict, percentile


def _verdict(p50, p99, statuses, casino_name="Goated"):
    return benchmark_verdict(p50=p50, p99=p99, statuses=statuses,
                             sample_count=len(statuses), casino_name=casino_name)


# ── percentile ────────────────────────────────────────────
def test_percentile_p50_midpoint():
    assert percentile(sorted([10.0, 20.0, 30.0, 40.0, 50.0]), 0.50) == 30.0


def test_percentile_p99_last():
    assert percentile(list(range(100)), 0.99) >= 98


def test_percentile_empty_zero():
    assert percentile([], 0.5) == 0.0


def test_percentile_single_sample():
    assert percentile([42.0], 0.5) == 42.0


def test_percentile_clamps_to_last_index():
    assert percentile([1, 2, 3], 2.0) == 3


def test_percentile_negative_clamps_to_first_index():
    assert percentile([1, 2, 3], -1.0) == 1


# ── benchmark_verdict ────────────────────────────────────
def test_verdict_rate_limited():
    colour, msg = _verdict(44, 100, [429, 200, 200, 429])
    assert colour == "red"
    assert "Rate-limited (2/4 got 429)" in msg


def test_verdict_network_bottleneck():
    colour, msg = _verdict(150, 300, [200] * 10)
    assert colour == "yellow"
    assert "Network bottleneck" in msg


def test_verdict_tail_latency():
    colour, msg = _verdict(40, 200, [200] * 10)
    assert colour == "yellow"
    assert "Tail-latency" in msg


def test_verdict_network_fast():
    colour, msg = _verdict(20, 25, [200] * 10)
    assert colour == "green"
    assert "Network fast" in msg


def test_verdict_network_ok():
    colour, msg = _verdict(50, 80, [200] * 10)
    assert colour == "cyan"
    assert "Network OK" in msg


def test_verdict_rate_limited_precedes_network_bottleneck():
    colour, msg = _verdict(150, 300, [429, 429, 200])
    assert colour == "red"
    assert "Rate-limited" in msg


def test_verdict_bottleneck_boundary_is_inclusive():
    assert _verdict(100, 100, [200])[0] == "yellow"
    assert _verdict(99, 100, [200])[0] == "cyan"


def test_verdict_fast_boundary_is_exclusive():
    assert _verdict(29, 30, [200])[0] == "green"
    assert _verdict(30, 30, [200])[0] == "cyan"


def test_verdict_names_the_casino():
    for statuses, p50 in (([429], 40), ([200], 150)):
        _, msg = _verdict(p50, p50, statuses, casino_name="Shuffle")
        assert "Shuffle" in msg
        assert "WolfBet" not in msg
