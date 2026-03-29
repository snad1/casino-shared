"""Tests for shared/core/strategy.py — rule engine."""

import json

import pytest

from shared.core.strategy import (
    RULE_ACTIONS, RULE_ACTION_LABELS, SEQ_MODES,
    StrategyRule, describe_rule, cmp, load_rules_from_text,
    make_lookups,
)


class TestMakeLookups:
    def test_creates_name_lookup(self):
        strategies = {"1": ("Flat Bet", "desc"), "2": ("Martingale", "desc")}
        names, by_name = make_lookups(strategies)
        assert names == {"1": "Flat Bet", "2": "Martingale"}
        assert by_name["flatbet"] == "1"
        assert by_name["martingale"] == "2"

    def test_normalizes_names(self):
        strategies = {"5": ("Paroli (3-step)", "desc")}
        _, by_name = make_lookups(strategies)
        assert "paroli3step" in by_name


class TestRuleActions:
    def test_all_actions_have_labels(self):
        for key, (name, label) in RULE_ACTIONS.items():
            assert name in RULE_ACTION_LABELS
            assert RULE_ACTION_LABELS[name] == label

    def test_21_actions(self):
        assert len(RULE_ACTIONS) == 21


class TestSeqModes:
    def test_5_modes(self):
        assert len(SEQ_MODES) == 5
        mode_names = {v[0] for v in SEQ_MODES.values()}
        assert "every" in mode_names
        assert "first_streak" in mode_names


class TestStrategyRule:
    def test_default_init(self):
        r = StrategyRule()
        assert r.cond_type == ""
        assert r.action_value == 0.0

    def test_normalizes_triggers(self):
        assert StrategyRule(cond_trigger="lose").cond_trigger == "loss"
        assert StrategyRule(cond_trigger="wins").cond_trigger == "win"
        assert StrategyRule(cond_trigger="losses").cond_trigger == "loss"
        assert StrategyRule(cond_trigger="bets").cond_trigger == "bet"

    def test_to_dict_roundtrip(self):
        r = StrategyRule(cond_type="sequence", cond_mode="every", cond_value=3.0,
                         cond_trigger="loss", action="reset_amount")
        d = r.to_dict()
        r2 = StrategyRule.from_dict(d)
        assert r2.cond_type == "sequence"
        assert r2.cond_mode == "every"
        assert r2.cond_value == 3.0
        assert r2.action == "reset_amount"

    def test_from_dict_missing_fields(self):
        r = StrategyRule.from_dict({"cond_type": "profit"})
        assert r.cond_type == "profit"
        assert r.action == ""


class TestDescribeRule:
    def test_sequence_rule(self):
        r = StrategyRule(cond_type="sequence", cond_mode="every", cond_value=3,
                         cond_trigger="loss", action="reset_amount")
        desc = describe_rule(r)
        assert "Every 3 loss(s)" in desc
        assert "Reset bet amount" in desc

    def test_profit_rule(self):
        r = StrategyRule(cond_type="profit", cond_field="session_profit",
                         cond_mode="gte", cond_value=0.01, action="stop")
        desc = describe_rule(r)
        assert ">=" in desc
        assert "Stop" in desc

    def test_bet_rule(self):
        r = StrategyRule(cond_type="bet", cond_field="amount",
                         cond_mode="gt", cond_value=1.0, action="stop")
        desc = describe_rule(r)
        assert "bet" in desc.lower()

    def test_action_value_shown(self):
        r = StrategyRule(cond_type="sequence", cond_mode="every", cond_value=1,
                         cond_trigger="loss", action="increase_amount", action_value=100)
        desc = describe_rule(r)
        assert "100" in desc


class TestCmp:
    def test_gte(self):
        assert cmp(5.0, "gte", 5.0) is True
        assert cmp(4.9, "gte", 5.0) is False

    def test_gt(self):
        assert cmp(5.1, "gt", 5.0) is True
        assert cmp(5.0, "gt", 5.0) is False

    def test_lte(self):
        assert cmp(5.0, "lte", 5.0) is True
        assert cmp(5.1, "lte", 5.0) is False

    def test_lt(self):
        assert cmp(4.9, "lt", 5.0) is True
        assert cmp(5.0, "lt", 5.0) is False

    def test_unknown_mode(self):
        assert cmp(5.0, "eq", 5.0) is False


class TestLoadRulesFromText:
    def test_empty_string(self):
        assert load_rules_from_text("") == []
        assert load_rules_from_text("   ") == []

    def test_json_format(self):
        rules = [{"cond_type": "sequence", "cond_mode": "every", "cond_value": 3,
                   "cond_trigger": "loss", "action": "reset_amount", "action_value": 0}]
        result = load_rules_from_text(json.dumps(rules))
        assert len(result) == 1
        assert result[0].cond_type == "sequence"
        assert result[0].description != ""

    def test_legacy_text_format(self):
        text = "onEvery3loseReset\nonEvery5winStop"
        result = load_rules_from_text(text)
        assert len(result) == 2
        assert result[0].cond_trigger == "loss"
        assert result[0].action == "reset_amount"
        assert result[1].cond_trigger == "win"
        assert result[1].action == "stop"

    def test_legacy_increase_with_percent(self):
        result = load_rules_from_text("onEvery1loseIncrease 100%")
        assert len(result) == 1
        assert result[0].action == "increase_amount"
        assert result[0].action_value == 100.0

    def test_skips_comments_and_blanks(self):
        text = "# comment\n\nonEvery1loseReset"
        result = load_rules_from_text(text)
        assert len(result) == 1

    def test_invalid_json_falls_through(self):
        result = load_rules_from_text("[invalid json")
        assert result == []
