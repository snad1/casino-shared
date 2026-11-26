"""The shared Telegram lane commands, bound to a bot other than WolfBet: they run that bot's
ctl, read that bot's vault, gate every command and button on the owner, and never read a
secret. WolfBet's own tg/ tests cover the full behaviour through the same module.
"""

import asyncio
import json
import logging
import subprocess
import types

import pytest

import shared.core.vault as vault
from shared.tg import lanes

OWNER = 4242
CTL = "/home/bot/.local/bin/cgctl"
LANES = {"v": 1, "lanes": [
    {"lane": 1, "alive": True, "paused": False, "state": {
        "lane_name": None, "key_ref": "default", "session_id": 9, "profit": 0.5, "currency": "btc"}},
    {"lane": 2, "alive": True, "paused": False, "state": {
        "lane_name": "friend-A", "key_ref": "friend", "session_id": 3, "profit": -0.1,
        "currency": "ltc"}},
]}
SESSION = {"id": 14, "lane": 2, "started_at": "2026-11-25T12:04:00", "ended_at": "2026-11-25T12:31:00",
           "total_bets": 9812, "wins": 5000, "losses": 4812, "wagered": 1.5, "profit": 0.00012,
           "currency": "USDT", "start_balance": 1.0, "end_balance": 1.00012, "strategy": "martingale",
           "multiplier": 2.0, "base_bet": 0.0001}


class Ctl:
    def __init__(self):
        self.calls, self.out, self.rc = [], json.dumps(LANES), 0

    def run(self, cmd, **kw):
        self.calls.append((cmd, kw))
        return types.SimpleNamespace(returncode=self.rc, stdout=self.out, stderr="")


@pytest.fixture
def ctl(monkeypatch):
    fake = Ctl()
    monkeypatch.setattr(lanes.subprocess, "run", fake.run)
    return fake


def _site(dashboard=None, **extra):
    return lanes.LaneSite(
        service="cryptogames", ctl_name="cgctl", ctl_path=lambda: CTL,
        is_owner=lambda user_id: user_id == OWNER, app_env=lambda: "production",
        key_label=lambda ref: {"friend": "friend (...a1b2)"}.get(ref, ref),
        dashboard_page=lambda path: f"{dashboard}/{path}" if dashboard else None,
        logger=logging.getLogger("test"), **extra)


def _update(user_id=OWNER, args=()):
    sent = []

    async def reply_text(text, **kw):
        sent.append((text, kw))

    upd = types.SimpleNamespace(effective_user=types.SimpleNamespace(id=user_id),
                                message=types.SimpleNamespace(reply_text=reply_text))
    return upd, types.SimpleNamespace(args=list(args)), sent


def _query(data, user_id=OWNER):
    q = types.SimpleNamespace(data=data, from_user=types.SimpleNamespace(id=user_id),
                              answers=[], edits=[])

    async def answer(text=None, **kw):
        q.answers.append(text)

    async def edit_message_text(text, **kw):
        q.edits.append((text, kw))

    q.answer, q.edit_message_text = answer, edit_message_text
    return q


def _buttons(markup):
    return [(b.text, b.callback_data or b.url) for row in markup.inline_keyboard for b in row]


def test_another_chat_is_refused_and_nothing_runs(ctl):
    cmds = lanes.LaneCommands(_site())
    for command in (cmds.cmd_lanes, cmds.cmd_sessions, cmds.cmd_laneadd, cmds.cmd_lanerename):
        upd, context, sent = _update(OWNER + 1, args=["2", "x"])
        asyncio.run(command(upd, context))
        assert sent == [(lanes.LANES_DENIED, {})]
    q = _query("lanego:stop:2", OWNER + 1)
    assert asyncio.run(cmds.handle_callback(q)) is True
    assert q.answers == [lanes.LANES_DENIED] and ctl.calls == []


def test_lanes_runs_this_bots_ctl_and_names_the_key(ctl):
    upd, context, sent = _update()
    asyncio.run(lanes.LaneCommands(_site()).cmd_lanes(upd, context))
    (cmd, kw), = ctl.calls
    assert cmd == [CTL, "lanes", "--json"] and kw["timeout"] == 10 and "shell" not in kw
    text, opts = sent[0]
    assert text.splitlines() == ["Lane 1: running, key default, session 9, +0.50000000 BTC",
                                 "Lane 2 · friend-A: running, key friend (...a1b2), session 3, "
                                 "-0.10000000 LTC"]
    assert ("Pause 2", "lane:pause:2") in _buttons(opts["reply_markup"])


def test_a_lane_button_runs_one_verb_on_that_lane_only(ctl):
    q = _query("lanego:pause:2")
    asyncio.run(lanes.LaneCommands(_site()).handle_callback(q))
    assert [c for c, _ in ctl.calls] == [[CTL, "lanes", "--json"], [CTL, "pause", "2"],
                                         [CTL, "lanes", "--json"]]
    assert ctl.calls[1][1]["timeout"] == 30


class _NoSecretVault:
    services = []

    def __init__(self, service):
        self.services.append(service)

    def keys(self):
        return [types.SimpleNamespace(key_id="friend", label="friend", last4="a1b2", kind="key"),
                types.SimpleNamespace(key_id="home", label="home", last4="9f3c", kind="proxy")]

    def secret(self, *a, **k):
        raise AssertionError("the picker must never read a secret")


def test_the_key_picker_reads_this_bots_vault_by_label_and_last_four(ctl, monkeypatch):
    monkeypatch.setattr(vault, "Vault", _NoSecretVault)
    q = _query("lane:key:2")
    asyncio.run(lanes.LaneCommands(_site()).handle_callback(q))
    text, opts = q.edits[-1]
    assert _NoSecretVault.services == ["cryptogames"]
    assert text == f"Pick a key for lane 2. {lanes.ADD_ON_DASHBOARD}"
    assert _buttons(opts["reply_markup"]) == [("friend (...a1b2)", "lanepick:key:2:friend"),
                                             ("Cancel", "lanes_refresh")]


def test_the_picker_links_this_bots_keys_page_when_a_dashboard_is_set(ctl, monkeypatch):
    monkeypatch.setattr(vault, "Vault", _NoSecretVault)
    q = _query("lane:proxy:2")
    asyncio.run(lanes.LaneCommands(_site("https://dash.example")).handle_callback(q))
    assert ("Open dashboard", "https://dash.example/cryptogames/keys") in _buttons(q.edits[-1][1]["reply_markup"])


def test_sessions_page_one_lane_through_this_bots_ctl(ctl):
    ctl.out = json.dumps({"v": 1, "sessions": [SESSION]})
    upd, context, sent = _update(args=["2"])
    asyncio.run(lanes.LaneCommands(_site()).cmd_sessions(upd, context))
    assert ctl.calls[0][0] == [CTL, "stats", "--json", "--lane", "2"]
    assert sent[0][0] == "#14 · 11-25 12:04-12:31 · 9,812 bets · +0.00012000 USDT"


def test_no_database_means_no_sessions(ctl):
    ctl.out = json.dumps({"v": 1, "error": {"code": "no_database", "message": "No database found."}})
    upd, context, sent = _update(args=["2"])
    asyncio.run(lanes.LaneCommands(_site()).cmd_sessions(upd, context))
    assert sent[0][0] == "No sessions on lane 2 yet."


def test_a_session_without_a_dashboard_has_no_link(ctl):
    ctl.out = json.dumps({"v": 1, "sessions": [SESSION]})
    q = _query("sessd:2:14")
    asyncio.run(lanes.LaneCommands(_site()).handle_callback(q))
    text, opts = q.edits[-1]
    assert text.startswith("Session #14 · lane 2")
    assert _buttons(opts["reply_markup"]) == [("Back", "sess:2:0")]


def test_a_failed_read_names_this_bots_ctl_without_a_traceback(ctl, monkeypatch):
    def hang(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 30)

    q = _query("lanego:stop:2")
    cmds = lanes.LaneCommands(_site())
    monkeypatch.setattr(cmds, "_read_lanes", lambda: asyncio.sleep(0, result=LANES))
    monkeypatch.setattr(lanes.subprocess, "run", hang)
    asyncio.run(cmds._lane_callback(q))
    assert q.answers == [None]
    assert asyncio.run(cmds._run_ctl(["stop", "2"], 2, "stop")) == (
        False, "Lane 2: cgctl did not answer. Check /lanes again.")


@pytest.mark.parametrize("data", ["noop", "slot:1", "", None])
def test_other_buttons_are_left_to_the_bot(ctl, data):
    q = _query(data)
    assert asyncio.run(lanes.LaneCommands(_site()).handle_callback(q)) is False
    assert q.answers == [] and ctl.calls == []


def test_lane_names_follow_the_cli_rule():
    assert lanes.lane_name_ok("night-run") and lanes.lane_name_ok("friend A")
    assert not lanes.lane_name_ok("-x") and not lanes.lane_name_ok(" x") and not lanes.lane_name_ok("a" * 33)


class _AccountVault(_NoSecretVault):
    def keys(self):
        return [types.SimpleNamespace(key_id="main", label="Stake main login", last4="9f3c", kind="account"),
                types.SimpleNamespace(key_id="stray", label="stray", last4="0000", kind="key")]


def test_a_bot_whose_lanes_bet_from_accounts_lists_the_accounts(ctl, monkeypatch):
    monkeypatch.setattr(vault, "Vault", _AccountVault)
    monkeypatch.setattr(vault, "vault_info", lambda service, ref, log=None: types.SimpleNamespace(
        key_id=ref, label="Stake main login", last4="9f3c", kind="account"))
    cmds = lanes.LaneCommands(_site(key_kind="account"))
    q = _query("lane:key:2")
    asyncio.run(cmds.handle_callback(q))
    assert _buttons(q.edits[-1][1]["reply_markup"]) == [
        ("Stake main login (...9f3c)", "lanepick:key:2:main"), ("Cancel", "lanes_refresh")]
    q = _query("lanepick:key:2:main")
    asyncio.run(cmds.handle_callback(q))
    assert q.edits[-1][0] == "Use Stake main login (...9f3c) on lane 2? Lane 2 restarts."
