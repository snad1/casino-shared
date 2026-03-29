"""Rule engine, strategy rules, and rule parsing.

Each bot defines its own STRATEGIES dict and imports everything else from here.
"""

import re
import json
from typing import List


def make_lookups(strategies: dict):
    """Create STRATEGY_NAMES and STRATEGY_BY_NAME from a STRATEGIES dict."""
    names = {k: v[0] for k, v in strategies.items()}
    by_name = {
        v[0].lower().replace("-", "").replace(" ", "").replace("(", "").replace(")", ""): k
        for k, v in strategies.items()
    }
    return names, by_name


# ── Rule actions ─────────────────────────────────────────
RULE_ACTIONS = {
    "1":  ("reset_amount",    "Reset bet amount"),
    "2":  ("increase_amount", "Increase amount by %"),
    "3":  ("decrease_amount", "Decrease amount by %"),
    "4":  ("add_amount",      "Add to amount"),
    "5":  ("deduct_amount",   "Deduct from amount"),
    "6":  ("set_amount",      "Set amount"),
    "7":  ("switch",          "Switch over/under"),
    "8":  ("stop",            "Stop betting"),
    "9":  ("reset_winchance", "Reset win chance"),
    "10": ("set_winchance",   "Set win chance"),
    "11": ("increase_wc",     "Increase win chance by %"),
    "12": ("decrease_wc",     "Decrease win chance by %"),
    "13": ("add_wc",          "Add to win chance"),
    "14": ("deduct_wc",       "Deduct from win chance"),
    "15": ("reset_payout",    "Reset payout"),
    "16": ("set_payout",      "Set payout"),
    "17": ("increase_payout", "Increase payout by %"),
    "18": ("decrease_payout", "Decrease payout by %"),
    "19": ("add_payout",      "Add to payout"),
    "20": ("deduct_payout",   "Deduct from payout"),
    "21": ("reset_game",      "Reset game (full reset)"),
}

RULE_ACTION_LABELS = {v[0]: v[1] for v in RULE_ACTIONS.values()}


# ── Sequential condition modes ───────────────────────────
SEQ_MODES = {
    "1": ("every",         "Every N (total count)"),
    "2": ("every_streak",  "Every N in streak (N, 2N, 3N…)"),
    "3": ("first_streak",  "First streak of N (once, resets on flip)"),
    "4": ("streak_above",  "Streak above N (every bet past N)"),
    "5": ("streak_below",  "Streak below N"),
}


class StrategyRule:
    __slots__ = ("cond_type", "cond_field", "cond_mode", "cond_value",
                 "cond_trigger", "action", "action_value", "description")

    def __init__(self, cond_type="", cond_field="", cond_mode="", cond_value=0.0,
                 cond_trigger="", action="", action_value=0.0, description=""):
        self.cond_type    = cond_type
        self.cond_field   = cond_field
        self.cond_mode    = cond_mode
        self.cond_value   = cond_value
        t = cond_trigger
        if t == "lose":  t = "loss"
        if t == "wins":  t = "win"
        if t == "losses": t = "loss"
        if t == "bets":  t = "bet"
        self.cond_trigger = t
        self.action       = action
        self.action_value = action_value
        self.description  = description

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyRule":
        return cls(**{k: d.get(k, "") for k in cls.__slots__})


def describe_rule(r: StrategyRule) -> str:
    if r.cond_type == "sequence":
        ml = dict(
            every="Every", every_streak="Every streak of",
            first_streak="First streak of",
            streak_above="Streak above", streak_below="Streak below",
        ).get(r.cond_mode, r.cond_mode)
        cond = f"{ml} {int(r.cond_value)} {r.cond_trigger}(s)"
    elif r.cond_type == "profit":
        op = dict(gte=">=", gt=">", lte="<=", lt="<").get(r.cond_mode, r.cond_mode)
        cond = f"On {r.cond_field} {op} {r.cond_value}"
    elif r.cond_type == "bet":
        op = dict(gte=">=", gt=">", lte="<=", lt="<").get(r.cond_mode, r.cond_mode)
        cond = f"On bet {r.cond_field} {op} {r.cond_value}"
    else:
        cond = "?"
    act = RULE_ACTION_LABELS.get(r.action, r.action)
    if r.action_value:
        act = f"{act} {r.action_value}"
    return f"{cond} → {act}"


def cmp(actual: float, mode: str, threshold: float) -> bool:
    if mode == "gte": return actual >= threshold
    if mode == "gt":  return actual > threshold
    if mode == "lte": return actual <= threshold
    if mode == "lt":  return actual < threshold
    return False


def load_rules_from_text(text: str) -> List[StrategyRule]:
    text = text.strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
            rules = []
            for d in data:
                r = StrategyRule.from_dict(d)
                if not r.description:
                    r.description = describe_rule(r)
                rules.append(r)
            return rules
        except (json.JSONDecodeError, TypeError):
            pass
    rules = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'onEvery(\d+)(win|lose|bet)(Reset|Stop|Increase|Switch)\s*(.*)', line, re.IGNORECASE)
        if not m:
            continue
        n = int(m.group(1))
        trigger = m.group(2).lower()
        action = m.group(3).lower()
        params = m.group(4).strip()
        value = 0.0
        if action == "increase":
            pct = re.search(r'(\d+(?:\.\d+)?)%', params)
            if pct: value = float(pct.group(1))
        action_map = {"reset": "reset_amount", "stop": "stop",
                      "increase": "increase_amount", "switch": "switch"}
        r = StrategyRule(
            cond_type="sequence", cond_mode="every", cond_value=float(n),
            cond_trigger=trigger if trigger != "lose" else "loss",
            action=action_map.get(action, action), action_value=value,
        )
        r.description = describe_rule(r)
        rules.append(r)
    return rules
