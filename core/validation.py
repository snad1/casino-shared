"""Numeric input validation shared by the bots' CLI and Telegram commands."""

import math


class InvalidNumber(ValueError):
    """A user-supplied number that cannot be used as a limit or target."""


def finite_float(name: str, value, minimum: float | None = None,
                 maximum: float | None = None) -> float:
    """Parse a finite float, rejecting nan, inf and out-of-range values.

    The message names the field only, so it is safe to show a Telegram user.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise InvalidNumber(f"{name} must be a number") from None
    if not math.isfinite(number):
        raise InvalidNumber(f"{name} must be a finite number")
    if minimum is not None and number < minimum:
        raise InvalidNumber(f"{name} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise InvalidNumber(f"{name} must be at most {maximum}")
    return number
