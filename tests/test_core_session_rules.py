"""Tests for shared/core/session_rules.py.

Cases ported from wolfbet/tests: test_phase2_helpers.py,
test_phase9_streak_reset.py, test_phase6a_clamp_visibility.py and
test_phase9b_insufficient_400.py (chain depth).
"""

import pytest

from shared.core.session_rules import (
    RECURRING_DELAY_MAX, STREAK_RESET_DELAY_MIN, STREAK_RESET_REASON,
    clamp, martingale_chain_depth, parse_streak_delay, record_outcome,
    restart_delay_for, should_restart, streak_delay_for,
    streak_reset_stop_reason, streak_reset_triggered, streakbet_loss_factor,
)


def _play(outcomes, streak=0, last_broken=0):
    """Feed outcomes through record_outcome like a bet loop does."""
    for o in outcomes:
        streak, broken = record_outcome(streak, o == "win")
        if broken is not None:
            last_broken = broken
    return streak, last_broken


class TestConstants:
    def test_values(self):
        assert STREAK_RESET_REASON == "Loss streak reset"
        assert RECURRING_DELAY_MAX == 86400
        assert STREAK_RESET_DELAY_MIN == 5


class TestRecordOutcome:
    def test_win_breaks_loss_streak(self):
        assert record_outcome(-5, True) == (1, 5)

    def test_win_after_win(self):
        assert record_outcome(3, True) == (4, 0)

    def test_first_bet_win(self):
        assert record_outcome(0, True) == (1, 0)

    def test_loss_leaves_last_broken_unchanged(self):
        assert record_outcome(-2, False) == (-3, None)
        assert record_outcome(4, False) == (-1, None)


class TestStreakReset:
    def test_fires_on_win_after_n_losses_at_profit(self):
        streak, broken = _play(["loss"] * 22 + ["win"])
        assert streak_reset_triggered(streak, broken, 20, 0.0001)
        reason = streak_reset_stop_reason(broken)
        assert reason.startswith(STREAK_RESET_REASON)
        assert "22 losses" in reason
        # Restarts even with recurring off, after the streak-reset delay.
        assert should_restart(reason, 0.0001, False, 20)
        assert restart_delay_for(reason, 60.0, 600.0) == 600.0

    def test_exactly_n_fires(self):
        streak, broken = _play(["loss"] * 20 + ["win"])
        assert streak_reset_triggered(streak, broken, 20, 0.0)

    def test_no_fire_when_in_red(self):
        streak, broken = _play(["loss"] * 20 + ["win"])
        assert not streak_reset_triggered(streak, broken, 20, -0.5)

    def test_no_fire_below_threshold(self):
        streak, broken = _play(["loss"] * 19 + ["win"])
        assert not streak_reset_triggered(streak, broken, 20, 0.1)

    @pytest.mark.parametrize("n", [0, None])
    def test_no_fire_when_off(self, n):
        streak, broken = _play(["loss"] * 30 + ["win"])
        assert not streak_reset_triggered(streak, broken, n, 0.1)

    def test_only_the_breaking_win_fires(self):
        streak, broken = _play(["loss"] * 20 + ["win", "win"])
        assert not streak_reset_triggered(streak, broken, 20, 0.1)

    def test_red_win_then_later_does_not_fire(self):
        streak, broken = _play(["loss"] * 20 + ["win"])
        assert not streak_reset_triggered(streak, broken, 20, -0.1)
        streak, broken = _play(["win"], streak, broken)
        assert not streak_reset_triggered(streak, broken, 20, 0.1)

    def test_reason_ignored_when_feature_off(self):
        reason = "Loss streak reset: 20 losses then win"
        assert not should_restart(reason, 0.1, True, 0)
        assert not should_restart(reason, 0.1, False, None)

    def test_streak_reset_in_red_never_restarts(self):
        assert not should_restart(streak_reset_stop_reason(20), -0.1, True, 20)


class TestShouldRestart:
    def test_recurring_disabled(self):
        assert not should_restart("Profit target reached: +1", 1.0, False, 0)

    def test_max_loss_never_restarts(self):
        assert not should_restart("Max loss hit: -1", 0.0, True, 0)

    def test_insufficient_balance_never_restarts(self):
        assert not should_restart("Insufficient balance", 0.0, True, 0)

    def test_profit_target_restarts(self):
        assert should_restart("Profit target reached: +1", 1.0, True, 0)

    def test_max_wins_restarts(self):
        assert should_restart("Max wins reached: 10", 0.0, True, 0)

    def test_max_bets_restarts(self):
        assert should_restart("Max bets reached: 100", 0.0, True, 0)

    def test_balance_floor_never_restarts(self):
        assert not should_restart("Balance floor hit: 0.1", 0.0, True, 0)

    def test_negative_profit_never_restarts(self):
        assert not should_restart("Max bets reached: 100", -0.01, True, 0)


class TestRestartDelay:
    def test_other_reasons_keep_recurring_delay(self):
        assert restart_delay_for("Profit target reached: +1", 60.0, 600.0) == 60.0

    def test_streak_reset_uses_its_delay(self):
        assert restart_delay_for(streak_reset_stop_reason(20), 60.0, 123.0) == 123.0


class TestClamp:
    def test_cap_survives(self):
        assert clamp("recurring_delay", 86400.0, 0.0, 86400.0) == (86400.0, None)

    def test_in_range_no_message(self):
        assert clamp("recurring_delay", 10800.0, 0.0, 86400.0) == (10800.0, None)

    def test_over_cap_clamps_with_message(self):
        v, msg = clamp("recurring_delay", 100000.0, 0.0, 86400.0)
        assert v == 86400.0
        assert msg == "⚠ recurring_delay clamped from 100000 to 86400 (allowed: 0..86400)"

    def test_negative_clamps_to_floor_with_message(self):
        v, msg = clamp("recurring_delay", -10.0, 0.0, 86400.0)
        assert v == 0.0
        assert msg.startswith("⚠") and "recurring_delay" in msg and "-10" in msg

    def test_tg_streak_reset_floor(self):
        v, msg = clamp("streak_reset_delay", 1.0, STREAK_RESET_DELAY_MIN, RECURRING_DELAY_MAX)
        assert v == 5
        assert "allowed: 5..86400" in msg


class TestParseStreakDelay:
    @pytest.mark.parametrize("raw", [None, "", "not-a-number", "5", "abc:def"])
    def test_empty_or_malformed(self, raw):
        assert parse_streak_delay(raw) == (0, 0.0)

    def test_valid(self):
        assert parse_streak_delay("5:1.0") == (5, 1.0)
        assert parse_streak_delay("3:0.5") == (3, 0.5)

    def test_split_once(self):
        assert parse_streak_delay("3:0.5:9") == (0, 0.0)


class TestStreakDelayFor:
    OFF = (0, 0.0)

    def test_loss_streak_triggers(self):
        assert streak_delay_for(-3, 3, (3, 0.5), self.OFF, self.OFF) == 0.5

    def test_win_streak_triggers(self):
        assert streak_delay_for(5, 5, self.OFF, (5, 1.0), self.OFF) == 1.0

    def test_every_n_bets_triggers(self):
        assert streak_delay_for(0, 20, self.OFF, self.OFF, (10, 0.2)) == 0.2

    def test_below_threshold_returns_zero(self):
        assert streak_delay_for(-2, 2, (5, 1.0), self.OFF, self.OFF) == 0.0

    def test_no_bets_yet(self):
        assert streak_delay_for(0, 0, self.OFF, self.OFF, (10, 0.2)) == 0.0

    def test_max_of_multiple(self):
        assert streak_delay_for(-3, 3, (3, 0.5), self.OFF, (3, 1.5)) == 1.5


class TestStreakbetLossFactor:
    def test_disabled(self):
        assert streakbet_loss_factor(-5, (0, 0.5)) == 1.0

    def test_on_loss_streak(self):
        assert streakbet_loss_factor(-3, (3, 0.5)) == 0.5
        assert streakbet_loss_factor(-6, (3, 0.5)) == 0.5

    def test_off_multiple(self):
        assert streakbet_loss_factor(-4, (3, 0.5)) == 1.0

    def test_win_streak(self):
        assert streakbet_loss_factor(3, (3, 0.5)) == 1.0


class TestMartingaleChainDepth:
    def test_basic(self):
        assert martingale_chain_depth(1.0, 0.001, 2.0) == 9

    def test_exact_boundary(self):
        # 1+2+4 = 7 base units fit exactly
        assert martingale_chain_depth(7.0, 1.0, 2.0) == 3

    def test_degenerate(self):
        assert martingale_chain_depth(0, 0.001, 2.0) == 0
        assert martingale_chain_depth(1.0, 0, 2.0) == 0
        assert martingale_chain_depth(1.0, 0.001, 1.0) == 0
