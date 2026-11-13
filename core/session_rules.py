"""Session restart and streak rules — pure functions, no bot state.

Ported from the WolfBet reference bot (wolfbet.py / core/net_helpers.py,
CLI v2.16.0–v2.32.1). Each service keeps its own state object and calls
these with plain values, so the logic exists once for every casino.
"""

import math
from typing import Optional, Tuple

# Stop-reason prefix for a streak reset; matched with str.startswith.
STREAK_RESET_REASON = "Loss streak reset"

# recurring_delay / streak_reset_delay cap (24 h), WolfBet v2.30.6.
RECURRING_DELAY_MAX = 86400
# Floor for the TG streak_reset_delay (same floor as TG recurring_delay).
STREAK_RESET_DELAY_MIN = 5

# Only these stop reasons may start a recurring cycle. Loss, balance
# floor and insufficient balance never restart.
_RECURRING_SAFE_PREFIXES = ("Profit target", "Max bets", "Max wins")


def record_outcome(current_streak: int, won: bool) -> Tuple[int, Optional[int]]:
    """Advance the streak after one bet.

    Returns (new_streak, last_broken_loss_streak). On a win the second value is
    the loss run the win just broke (0 if there was none); on a loss it is None,
    meaning the caller keeps its stored value unchanged.
    """
    if won:
        return max(current_streak, 0) + 1, max(-current_streak, 0)
    return min(current_streak, 0) - 1, None


def streak_reset_triggered(current_streak: int, last_broken: int,
                           n: Optional[int], profit: float) -> bool:
    """True only on the win that broke an N+ loss streak, while profit >= 0.

    n falsy (0 / None) means the feature is off.
    """
    return bool(n and current_streak == 1 and last_broken >= n and profit >= 0)


def streak_reset_stop_reason(last_broken: int) -> str:
    """Stop-reason text for a streak reset (WolfBet's exact wording)."""
    return f"{STREAK_RESET_REASON}: {last_broken} losses then win"


def should_restart(reason: str, profit: float, recurring: bool,
                   streak_reset_losses: Optional[int]) -> bool:
    """Whether a stop with this reason starts a new cycle.

    A session in the red never restarts. A streak reset restarts whenever the
    feature is on, independent of the recurring flag.
    """
    if profit < 0:
        return False
    if reason.startswith(STREAK_RESET_REASON):
        return bool(streak_reset_losses)
    if not recurring:
        return False
    return any(reason.startswith(p) for p in _RECURRING_SAFE_PREFIXES)


def restart_delay_for(reason: str, recurring_delay: float,
                      streak_reset_delay: float) -> float:
    """Pause before the next cycle: a streak reset has its own delay."""
    if reason.startswith(STREAK_RESET_REASON):
        return streak_reset_delay
    return recurring_delay


def clamp(name: str, value: float, lo: float, hi: float) -> Tuple[float, Optional[str]]:
    """Clamp value into [lo, hi]. Returns (clamped, message or None).

    The message tells the user what happened instead of silently swallowing
    out-of-range input.
    """
    clamped = max(lo, min(hi, value))
    if clamped != value:
        return clamped, (f"⚠ {name} clamped from {value:g} to {clamped:g} "
                         f"(allowed: {lo:g}..{hi:g})")
    return clamped, None


def parse_streak_delay(raw) -> Tuple[int, float]:
    """Parse the "N:X" grammar used by streak_delay_* and streakbet_loss.

    Returns (0, 0.0) on empty / malformed input; (int(N), float(X)) otherwise.
    """
    if not raw:
        return (0, 0.0)
    try:
        parts = str(raw).split(":", 1)
        return (int(parts[0]), float(parts[1]))
    except (ValueError, IndexError):
        return (0, 0.0)


def streak_delay_for(streak: int, total_bets: int,
                     loss: Tuple[int, float], win: Tuple[int, float],
                     bets: Tuple[int, float]) -> float:
    """Largest applicable streak delay in seconds, or 0.0.

    loss / win / bets are (N, seconds) pairs: pause every N-th loss in a row,
    win in a row, or bet overall. N = 0 disables that pair.
    """
    delay = 0.0
    if streak < 0 and loss[0] > 0 and abs(streak) % loss[0] == 0:
        delay = max(delay, loss[1])
    if streak > 0 and win[0] > 0 and streak % win[0] == 0:
        delay = max(delay, win[1])
    if bets[0] > 0 and total_bets > 0 and total_bets % bets[0] == 0:
        delay = max(delay, bets[1])
    return delay


def streakbet_loss_factor(streak: int, streakbet_loss: Tuple[int, float]) -> float:
    """Next-bet multiplier on every N-th loss in a row; 1.0 = no change."""
    count, mult = streakbet_loss
    if count > 0 and streak < 0 and abs(streak) % count == 0:
        return mult
    return 1.0


def martingale_chain_depth(balance: float, base: float, mult: float) -> int:
    """Longest loss run a Martingale chain can pay for.

    Max k with base·(mult^k − 1)/(mult − 1) <= balance. 0 when the inputs
    can't form a chain.
    """
    if balance <= 0 or base <= 0 or mult <= 1:
        return 0
    k = int(math.log(1 + balance * (mult - 1) / base) / math.log(mult))
    # guard float rounding at the boundary
    while k > 0 and base * (mult ** k - 1) / (mult - 1) > balance:
        k -= 1
    return k
