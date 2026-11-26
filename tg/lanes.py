"""Telegram lane commands for any bot: /lanes, one-lane buttons, /laneadd, /lanerename, the
key and proxy picker and /sessions.

Each bot passes a `LaneSite` with its service name, its ctl and its owner check, then
registers the `LaneCommands` coroutines. Every lane change runs the bot's own ctl as an
argv list (never a shell) with a timeout, so there is no second copy of lane logic here.
No secret travels through the chat: pickers show a vault entry's label and last 4 only.
"""

import asyncio
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from shared.core.lanes import LANE_NAME_RE
from shared.core.vault import VaultError

LANES_DENIED = "Only the owner can manage lanes."
LANES_FAILED = "Could not read the lanes. Try again."
LANE_NAME_RULE = ("Lane names are 1-32 letters, digits, space, _ or -, "
                  "not starting with - or a space.")
LANE_ONE_KEPT = "Lane 1 is the main lane and can't be removed."
LANE_ONE_ACCOUNT = "Change lane 1's key or proxy on the dashboard Config page."
VAULT_FAILED = "Could not read the saved keys. Try again."
ADD_ON_DASHBOARD = "Add keys on the dashboard Keys page."
SESSIONS_FAILED = "Could not read the sessions. Try again."
SESSIONS_USAGE = "Usage: /sessions [lane]"
SESSIONS_PAGE = 10

_LANE_READ_ERRORS = (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError)
_REFRESH_ROW = [InlineKeyboardButton("Refresh", callback_data="lanes_refresh")]
_CANCEL = InlineKeyboardButton("Cancel", callback_data="lanes_refresh")

_LANE_CALLBACK = re.compile(
    r"^(lane|lanego):(pause|resume|stop|start|restart|restart_end|remove|remove_end|key|proxy)"
    r":([1-9][0-9]?)$")
_LANE_NAMED_CALLBACK = re.compile(r"^(laneadd|lanern):([1-9][0-9]?):(.{1,32})$")
_ACCOUNT_CALLBACK = re.compile(
    r"^(lanepick|laneuse|laneuse_end):(key|proxy):([1-9][0-9]?):([a-z0-9][a-z0-9_-]{0,31}|-)$")
_SESSIONS_CALLBACK = re.compile(r"^(sess|sessd):([0-9]{1,2}):([0-9]{1,10})$")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_LANE_K = re.compile(r"^[1-9][0-9]?$")

_LANE_BUTTONS = {
    "running": ("pause", "restart", "stop"),
    "paused": ("resume", "restart", "stop"),
    "nostate": ("restart", "stop"),
    "stopped": ("start",),
}
_LANE_ARGV = {
    "pause": (("pause",), ()), "resume": (("resume",), ()), "stop": (("stop",), ()),
    "start": (("start",), ()), "restart": (("restart",), ()),
    "restart_end": (("restart",), ("--at-boundary",)),
    "remove": (("lane", "remove"), ()), "remove_end": (("lane", "remove"), ("--after-session",)),
}
_ALIVE = ("running", "paused", "nostate")
_LANE_NEEDS = {
    "pause": ("running",), "resume": ("paused",), "start": ("stopped",),
    "stop": _ALIVE, "restart": _ALIVE, "restart_end": _ALIVE,
    "remove": _ALIVE + ("stopped",), "remove_end": _ALIVE,
}
_LANE_IS = {"stopped": "already stopped", "paused": "already paused",
            "running": "already running", "nostate": "already running"}
_PICK_KINDS = ("key", "proxy")


# ── Formatting (plain text, so a lane name never needs escaping) ──────────

def format_lanes(data: dict, key_label) -> str:
    """One plain-text line per CLI lane from `<ctl> lanes --json`; `key_label(key_ref)` names
    the key without its secret."""
    lines = []
    for row in sorted(data.get("lanes") or [], key=lambda r: r.get("lane", 0)):
        lane, st = row.get("lane"), row.get("state")
        name = (st or {}).get("lane_name")
        title = f"Lane {lane} · {name}" if name else f"Lane {lane}"
        if not row.get("alive"):
            lines.append(f"{title}: stopped")
            continue
        if st is None:
            lines.append(f"{title}: no state yet")
            continue
        parts = ["paused" if row.get("paused") else "running",
                 f"key {key_label(st.get('key_ref') or 'default')}"]
        if st.get("session_id") is not None:
            parts.append(f"session {st['session_id']}")
        profit = float(st.get("profit") or 0)
        parts.append(f"{profit:+.8f} {str(st.get('currency') or '').upper()}".rstrip())
        lines.append(f"{title}: {', '.join(parts)}")
    return "\n".join(lines) if lines else "No lanes."


def _cli_time(value):
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _cli_span(s: dict) -> str:
    """`11-25 12:04-12:31`, `11-25 23:50-11-26 00:20` across a day, or `11-25 12:04-running`."""
    start, end = _cli_time(s.get("started_at")), _cli_time(s.get("ended_at"))
    if start is None:
        return "?"
    head = start.strftime("%m-%d %H:%M")
    if end is None:
        return f"{head}-running"
    return f"{head}-{end.strftime('%H:%M' if end.date() == start.date() else '%m-%d %H:%M')}"


def _cli_profit(s: dict) -> str:
    return f"{float(s.get('profit') or 0):+.8f} {str(s.get('currency') or '').upper()}".rstrip()


def format_cli_session_line(s: dict, show_lane: bool) -> str:
    """One line per CLI session from `<ctl> stats --json`."""
    lane = int(s.get("lane") or 1)
    tag = f"L{lane} · " if show_lane and lane > 1 else ""
    bets = int(s.get("total_bets") or 0)
    return f"{tag}#{s.get('id')} · {_cli_span(s)} · {bets:,} bets · {_cli_profit(s)}"


def format_cli_session_detail(s: dict) -> str:
    """Plain-text detail of one CLI session from `<ctl> stats --json`."""
    start, end = _cli_time(s.get("started_at")), _cli_time(s.get("ended_at"))
    bets, wins, losses = (int(s.get(k) or 0) for k in ("total_bets", "wins", "losses"))
    rate = f"{wins / bets * 100:.1f}%" if bets else "-"
    lines = [f"Session #{s.get('id')} · lane {int(s.get('lane') or 1)}",
             f"Start: {start.isoformat(' ', 'seconds') if start else '?'}",
             f"End: {end.isoformat(' ', 'seconds') if end else 'running'}"]
    if start and end:
        total = max(int((end - start).total_seconds()), 0)
        h, rem = divmod(total, 3600)
        lines.append(f"Duration: {h}h {rem // 60}m {rem % 60}s")
    cur = str(s.get("currency") or "").upper()
    lines += [
        f"Bets: {bets:,} (W {wins:,} / L {losses:,}, win rate {rate})",
        f"Wagered: {float(s.get('wagered') or 0):.8f} {cur}".rstrip(),
        f"Profit: {_cli_profit(s)}",
        f"Balance: {float(s.get('start_balance') or 0):.8f} -> {float(s.get('end_balance') or 0):.8f}",
        f"Strategy: {s.get('strategy') or '?'}, {float(s.get('multiplier') or 0):g}x, "
        f"base {float(s.get('base_bet') or 0):.8f}",
    ]
    return "\n".join(lines)


# ── Pure helpers ──────────────────────────────────────────────────────────

def vault_label(info) -> str:
    return f"{info.label} (...{info.last4})"


def lane_state(row: dict) -> str:
    """`stopped`, `paused`, `running` or `nostate` for one `<ctl> lanes --json` row."""
    if not row.get("alive"):
        return "stopped"
    if row.get("state") is None:
        return "nostate"
    return "paused" if row.get("paused") else "running"


def lane_buttons(data: dict) -> list:
    """One row of (label, callback_data) per lane, in lane order, for the actions its state allows."""
    rows = []
    for row in sorted(data.get("lanes") or [], key=lambda r: r.get("lane", 0)):
        k = row.get("lane")
        actions = _LANE_BUTTONS[lane_state(row)] + (("remove",) if k != 1 else ())
        rows.append([(f"{action.capitalize()} {k}", f"lane:{action}:{k}") for action in actions])
        if k != 1:
            rows.append([(f"Key {k}", f"lane:key:{k}"), (f"Proxy {k}", f"lane:proxy:{k}")])
    return rows


def lane_name_ok(name: str) -> bool:
    """The CLI's lane-name rule; a leading - would also reach the ctl as an option."""
    return bool(LANE_NAME_RE.match(name)) and not name.startswith(("-", " "))


def _parse_lane_callback(data: str):
    """(stage, action, lane) from trusted-shape callback data, else None."""
    m = _LANE_CALLBACK.match(data or "")
    return (m.group(1), m.group(2), int(m.group(3))) if m else None


def _parse_named_callback(data: str):
    """(verb, lane, name) from `laneadd:K:NAME` or `lanern:K:NAME`, else None."""
    m = _LANE_NAMED_CALLBACK.match(data or "")
    if not m or not lane_name_ok(m.group(3)):
        return None
    return m.group(1), int(m.group(2)), m.group(3)


def _parse_account_callback(data: str):
    """(stage, kind, lane, ref or None for no proxy) from account button data, else None."""
    m = _ACCOUNT_CALLBACK.match(data or "")
    if not m or (m.group(4) == "-" and m.group(2) != "proxy"):
        return None
    return m.group(1), m.group(2), int(m.group(3)), (None if m.group(4) == "-" else m.group(4))


def _lane_confirm(action: str, k: int) -> tuple:
    """(question, buttons) asked before a lane action runs."""
    if action in ("restart", "remove"):
        verb = action.capitalize()
        return f"{verb} lane {k}?", InlineKeyboardMarkup([[
            InlineKeyboardButton(f"{verb} now" if action == "restart" else "Now",
                                 callback_data=f"lanego:{action}:{k}"),
            InlineKeyboardButton("At session end", callback_data=f"lanego:{action}_end:{k}"),
            _CANCEL]])
    verb = action.capitalize()
    question = f"{verb} lane {k}?"
    if action == "stop" and k == 1:
        question = "Stop lane 1? It is the main lane."
    return question, InlineKeyboardMarkup([[
        InlineKeyboardButton(verb, callback_data=f"lanego:{action}:{k}"), _CANCEL]])


def _named_confirm(verb: str, k: int, name: str) -> tuple:
    if verb == "laneadd":
        copying = f", copying lane {k}'s settings" if k != 1 else ""
        question, button = f"Add the lane {name}{copying}?", "Add"
    else:
        question, button = f"Rename lane {k} to {name}?", "Rename"
    return question, InlineKeyboardMarkup([[
        InlineKeyboardButton(button, callback_data=f"{verb}:{k}:{name}"), _CANCEL]])


def _parse_laneadd(args: list):
    """(source lane, name) from `/laneadd NAME [from K]` args, else None."""
    args = list(args or [])
    k = 1
    if len(args) >= 3 and args[-2].lower() == "from":
        if not _LANE_K.match(args[-1]):
            return None
        k, args = int(args[-1]), args[:-2]
    name = " ".join(args)
    return (k, name) if args else None


def _lane_row(data: dict, k: int):
    return next((r for r in data.get("lanes") or [] if r.get("lane") == k), None)


def _stale_reason(data: dict, action: str, k: int) -> Optional[str]:
    """Why `action` no longer fits lane `k` (it changed since the message), else None."""
    if action.startswith("remove") and k == 1:
        return LANE_ONE_KEPT
    row = _lane_row(data, k)
    if row is None:
        return f"Lane {k} is not configured."
    state = lane_state(row)
    if state in _LANE_NEEDS[action]:
        return None
    if action == "remove_end":
        return f"Lane {k} is stopped. Remove it with Now."
    if action == "resume":
        return f"Lane {k} is not paused."
    return f"Lane {k} is {_LANE_IS[state]}."


def _ctl_lines(text: str) -> list:
    lines = [_ANSI.sub("", line).strip() for line in (text or "").splitlines()]
    return [line for line in lines if line]


def _ctl_line(text: str, last: bool) -> str:
    lines = _ctl_lines(text)
    if not lines:
        return ""
    return (lines[-1] if last else lines[0])[:200]


def _no_sessions(lane: int) -> str:
    return f"No sessions on lane {lane} yet." if lane else "No sessions yet."


async def _edit_text(query, text: str, keyboard) -> None:
    try:
        await query.edit_message_text(text, reply_markup=keyboard)
    except BadRequest:
        pass


# ── The commands, bound to one bot ────────────────────────────────────────

@dataclass
class LaneSite:
    """What differs per bot. The callables are read at call time, so a bot's tests can
    replace the functions they point at."""
    service: str                                    # vault service and dashboard path prefix
    ctl_name: str                                   # the bot's management command
    ctl_path: Callable[[], Optional[str]]           # the installed ctl, or None
    is_owner: Callable[[int], bool]
    app_env: Callable[[], str]
    key_label: Callable[[str], str]                 # `label (...last4)` for a key id
    dashboard_page: Callable[[str], Optional[str]]  # a dashboard page URL, or None when unset
    logger: object


def default_key_label(service: str, key_ref: str, log=None) -> str:
    """A vault key as `label (...last4)`; the bare id when the vault doesn't hold it."""
    try:
        from shared.core import vault
        info = vault.vault_info(service, key_ref, log=log)
    except ImportError:
        info = None
    return vault_label(info) if info else key_ref


class LaneCommands:
    """The lane commands and buttons for one bot."""

    def __init__(self, site: LaneSite):
        self.site = site

    @property
    def _log(self):
        return self.site.logger

    def _error_suffix(self, e: Exception) -> str:
        return "" if self.site.app_env() == "production" else f" ({type(e).__name__})"

    async def _read_lanes(self) -> dict:
        """`<ctl> lanes --json` parsed; raises OSError, TimeoutExpired, RuntimeError or ValueError."""
        ctl = self.site.ctl_path()
        if not ctl:
            raise FileNotFoundError(self.site.ctl_name)
        result = await asyncio.to_thread(subprocess.run, [ctl, "lanes", "--json"],
                                         capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            raise RuntimeError(f"{self.site.ctl_name} lanes rc={result.returncode}")
        return json.loads(result.stdout)

    async def _lanes_view(self, note: str = "") -> tuple:
        """(text, keyboard) for the lane list, `note` first; Refresh only when the read fails."""
        try:
            data = await self._read_lanes()
        except _LANE_READ_ERRORS as e:
            self._log.warning("lanes: %s: %s", type(e).__name__, e)
            text = LANES_FAILED + self._error_suffix(e)
            return (f"{note}\n\n{text}" if note else text), InlineKeyboardMarkup([_REFRESH_ROW])
        text = format_lanes(data, self.site.key_label)
        keyboard = [[InlineKeyboardButton(label, callback_data=cb) for label, cb in row]
                    for row in lane_buttons(data)]
        return (f"{note}\n\n{text}" if note else text), InlineKeyboardMarkup(keyboard + [_REFRESH_ROW])

    async def cmd_lanes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List every CLI lane with its state, key label, session and action buttons (owner only)."""
        if not self.site.is_owner(update.effective_user.id):
            await update.message.reply_text(LANES_DENIED)
            return
        text, keyboard = await self._lanes_view()
        await update.message.reply_text(text, reply_markup=keyboard)

    async def _edit_lanes(self, query, note: str = "") -> None:
        text, keyboard = await self._lanes_view(note)
        try:
            await query.edit_message_text(text, reply_markup=keyboard)
        except BadRequest:
            pass

    async def _lanes_refresh(self, query) -> None:
        if not self.site.is_owner(query.from_user.id):
            await query.answer(LANES_DENIED, show_alert=True)
            return
        await query.answer()
        await self._edit_lanes(query)

    async def _run_ctl(self, args: list, k: int, label: str):
        """Run `<ctl> *args`; (ok, note) where a failure note is the ctl's own first line.

        The CLI prints its errors on stdout and bash usage errors on stderr, so both are read.
        """
        name = self.site.ctl_name
        argv = [self.site.ctl_path() or name, *args]
        try:
            result = await asyncio.to_thread(subprocess.run, argv, capture_output=True,
                                             text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            self._log.warning("lane %s %d: %s", label, k, type(e).__name__)
            return False, f"Lane {k}: {name} did not answer. Check /lanes again.{self._error_suffix(e)}"
        self._log.info("lane %s %d: rc=%d", label, k, result.returncode)
        if result.returncode != 0:
            return False, (_ctl_line(result.stderr, last=False)
                           or _ctl_line(result.stdout, last=False) or f"Lane {k}: {label} failed.")
        return True, result.stdout

    async def _run_lane_action(self, action: str, k: int) -> str:
        """Run one ctl verb on lane `k` only; the line the ctl printed, or a safe message."""
        try:
            data = await self._read_lanes()
        except _LANE_READ_ERRORS as e:
            self._log.warning("lane %s %d: %s", action, k, type(e).__name__)
            return LANES_FAILED + self._error_suffix(e)
        reason = _stale_reason(data, action, k)
        if reason:
            return reason
        before, after = _LANE_ARGV[action]
        ok, out = await self._run_ctl([*before, str(k), *after], k, action)
        return (_ctl_line(out, last=True) or f"Lane {k}: done.") if ok else out

    async def _run_named(self, verb: str, k: int, name: str) -> str:
        """Add a lane named `name` copying lane `k`, or rename lane `k`; the note to show."""
        if verb == "lanern":
            try:
                data = await self._read_lanes()
            except _LANE_READ_ERRORS as e:
                self._log.warning("lane rename %d: %s", k, type(e).__name__)
                return LANES_FAILED + self._error_suffix(e)
            if _lane_row(data, k) is None:
                return f"Lane {k} is not configured."
            ok, out = await self._run_ctl(["lane", "rename", str(k), name], k, "rename")
            return f"Lane {k} renamed to {name}." if ok else out
        ok, out = await self._run_ctl(["lane", "add", name, "--from", str(k)], k, "add")
        if not ok:
            return out
        lines = _ctl_lines(out)
        added = next((line for line in lines if line.startswith("Lane ")), "")
        note = " ".join(dict.fromkeys(line for line in (added, lines[-1] if lines else "") if line))
        return note[:200] or f"Lane {name} added."

    async def _lane_callback(self, query) -> None:
        if not self.site.is_owner(query.from_user.id):
            await query.answer(LANES_DENIED, show_alert=True)
            return
        parsed = _parse_lane_callback(query.data)
        if parsed is None:
            await query.answer("Unknown button.", show_alert=True)
            return
        stage, action, k = parsed
        if action in _PICK_KINDS:
            if stage != "lane":
                await query.answer("Unknown button.", show_alert=True)
                return
            await query.answer()
            await _edit_text(query, *(await self._account_picker(action, k)))
            return
        await query.answer()
        if stage == "lane":
            question, keyboard = _lane_confirm(action, k)
            try:
                await query.edit_message_text(question, reply_markup=keyboard)
            except BadRequest:
                pass
            return
        await self._edit_lanes(query, await self._run_lane_action(action, k))

    def _vault_entries(self, kind: str) -> list:
        """(id, `label (...last4)`) for every saved entry of `kind`; never a secret."""
        from shared.core import vault
        return [(info.key_id, vault_label(info)) for info in vault.Vault(self.site.service).keys()
                if info.kind == kind]

    def _vault_entry(self, kind: str, ref: str):
        """`label (...last4)` of saved entry `ref` when it is of `kind`, else None."""
        from shared.core import vault
        info = vault.vault_info(self.site.service, ref, log=self._log)
        return vault_label(info) if info is not None and info.kind == kind else None

    def _account_label(self, kind: str, ref):
        """The name to show for `ref` (None: no proxy), or None when the vault no longer has it."""
        return "no proxy" if ref is None else self._vault_entry(kind, ref)

    async def _account_picker(self, kind: str, k: int) -> tuple:
        """(text, keyboard) listing the saved keys or proxies lane `k` can switch to."""
        if k == 1:
            return LANE_ONE_ACCOUNT, InlineKeyboardMarkup([[_CANCEL]])
        try:
            entries = await asyncio.to_thread(self._vault_entries, kind)
        except (VaultError, OSError) as e:
            self._log.warning("lane %s picker %d: %s", kind, k, type(e).__name__)
            return VAULT_FAILED + self._error_suffix(e), InlineKeyboardMarkup([[_CANCEL]])
        word, words = ("key", "keys") if kind == "key" else ("proxy", "proxies")
        rows = [[InlineKeyboardButton(label, callback_data=f"lanepick:{kind}:{k}:{ref}")]
                for ref, label in entries]
        if kind == "proxy":
            rows.append([InlineKeyboardButton("No proxy", callback_data=f"lanepick:proxy:{k}:-")])
        if entries:
            text = f"Pick a {word} for lane {k}. {ADD_ON_DASHBOARD}"
        else:
            text = f"No saved {words} yet. {ADD_ON_DASHBOARD}"
        link = self.site.dashboard_page(f"{self.site.service}/keys")
        rows.append(([InlineKeyboardButton("Open dashboard", url=link)] if link else []) + [_CANCEL])
        return text, InlineKeyboardMarkup(rows)

    async def _run_account(self, kind: str, k: int, ref, at_end: bool) -> str:
        """Point lane `k` at saved entry `ref`, then restart lane `k` only; the note to show."""
        try:
            data = await self._read_lanes()
        except _LANE_READ_ERRORS as e:
            self._log.warning("lane %s %d: %s", kind, k, type(e).__name__)
            return LANES_FAILED + self._error_suffix(e)
        row = _lane_row(data, k)
        if row is None:
            return f"Lane {k} is not configured."
        label = await asyncio.to_thread(self._account_label, kind, ref)
        if label is None:
            return f"{ref} is no longer in the vault."
        if kind == "key" and (row.get("state") or {}).get("key_ref") == ref:
            return f"Lane {k} already uses {label}."
        ok, out = await self._run_ctl(["config-set", f"lane{k}.{kind}_ref={ref or 'off'}"], k, kind)
        if not ok:
            return out
        if lane_state(row) == "stopped":
            return f"Lane {k} uses {label} when it starts."
        ok, out = await self._run_ctl(["restart", str(k), *(["--at-boundary"] if at_end else [])],
                                      k, "restart")
        return (_ctl_line(out, last=True) or f"Lane {k} uses {label}.") if ok else out

    async def _account_callback(self, query) -> None:
        if not self.site.is_owner(query.from_user.id):
            await query.answer(LANES_DENIED, show_alert=True)
            return
        parsed = _parse_account_callback(query.data)
        if parsed is None:
            await query.answer("Unknown button.", show_alert=True)
            return
        stage, kind, k, ref = parsed
        await query.answer()
        if k == 1:
            await _edit_text(query, LANE_ONE_ACCOUNT, InlineKeyboardMarkup([[_CANCEL]]))
            return
        if stage != "lanepick":
            await self._edit_lanes(query, await self._run_account(kind, k, ref, stage == "laneuse_end"))
            return
        label = await asyncio.to_thread(self._account_label, kind, ref)
        if label is None:
            await self._edit_lanes(query, f"{ref} is no longer in the vault.")
            return
        tail = f"{kind}:{k}:{ref or '-'}"
        await _edit_text(query, f"Use {label} on lane {k}? Lane {k} restarts.", InlineKeyboardMarkup([[
            InlineKeyboardButton("Use now", callback_data=f"laneuse:{tail}"),
            InlineKeyboardButton("At session end", callback_data=f"laneuse_end:{tail}"),
            _CANCEL]]))

    async def _read_sessions(self, lane: int, before: Optional[int]) -> list:
        """`<ctl> stats --json` rows, newest first; lane 0 means every lane."""
        ctl = self.site.ctl_path()
        if not ctl:
            raise FileNotFoundError(self.site.ctl_name)
        argv = [ctl, "stats", "--json", *(["--lane", str(lane)] if lane else []),
                *(["--before", str(before)] if before else [])]
        result = await asyncio.to_thread(subprocess.run, argv, capture_output=True, text=True,
                                         timeout=10)
        if result.returncode != 0:
            try:
                if json.loads(result.stdout)["error"]["code"] == "no_database":
                    return []
            except (ValueError, KeyError, TypeError):
                pass
            raise RuntimeError(f"{self.site.ctl_name} stats rc={result.returncode}")
        return list(json.loads(result.stdout).get("sessions") or [])

    async def _sessions_view(self, lane: int, before: Optional[int] = None) -> tuple:
        """(text, keyboard) for one page of CLI sessions."""
        try:
            rows = await self._read_sessions(lane, before)
        except _LANE_READ_ERRORS as e:
            self._log.warning("sessions: %s", type(e).__name__)
            return SESSIONS_FAILED + self._error_suffix(e), None
        page = rows[:SESSIONS_PAGE]
        if not page:
            return _no_sessions(lane), None
        text = "\n".join(format_cli_session_line(s, show_lane=not lane) for s in page)
        ids = [s.get("id") for s in page]
        keyboard = [[InlineKeyboardButton(f"#{sid}", callback_data=f"sessd:{lane}:{sid}")
                     for sid in ids[i:i + 5]] for i in range(0, len(ids), 5)]
        if len(rows) > SESSIONS_PAGE:
            keyboard.append([InlineKeyboardButton("Older", callback_data=f"sess:{lane}:{ids[-1]}")])
        return text, InlineKeyboardMarkup(keyboard)

    async def _session_detail(self, lane: int, sid: int) -> tuple:
        try:
            rows = await self._read_sessions(lane, sid + 1)
        except _LANE_READ_ERRORS as e:
            self._log.warning("session %d: %s", sid, type(e).__name__)
            return SESSIONS_FAILED + self._error_suffix(e), None
        row = next((s for s in rows[:1] if s.get("id") == sid), None)
        back = InlineKeyboardButton("Back", callback_data=f"sess:{lane}:0")
        if row is None:
            return f"Session #{sid} was not found.", InlineKeyboardMarkup([[back]])
        link = self.site.dashboard_page(f"{self.site.service}/sessions")
        buttons = ([InlineKeyboardButton("Open on dashboard", url=link)] if link else []) + [back]
        return format_cli_session_detail(row), InlineKeyboardMarkup([buttons])

    async def cmd_sessions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Recent CLI-lane sessions with their ids, 10 per page (owner only)."""
        if not self.site.is_owner(update.effective_user.id):
            await update.message.reply_text(LANES_DENIED)
            return
        args = list(context.args or [])
        if len(args) > 1 or (args and not _LANE_K.match(args[0])):
            await update.message.reply_text(SESSIONS_USAGE)
            return
        text, keyboard = await self._sessions_view(int(args[0]) if args else 0)
        await update.message.reply_text(text, reply_markup=keyboard)

    async def _sessions_callback(self, query) -> None:
        if not self.site.is_owner(query.from_user.id):
            await query.answer(LANES_DENIED, show_alert=True)
            return
        m = _SESSIONS_CALLBACK.match(query.data or "")
        if not m:
            await query.answer("Unknown button.", show_alert=True)
            return
        stage, lane, n = m.group(1), int(m.group(2)), int(m.group(3))
        await query.answer()
        if stage == "sessd":
            text, keyboard = await self._session_detail(lane, n)
        else:
            text, keyboard = await self._sessions_view(lane, n or None)
        await _edit_text(query, text, keyboard)

    async def _named_lane_callback(self, query) -> None:
        if not self.site.is_owner(query.from_user.id):
            await query.answer(LANES_DENIED, show_alert=True)
            return
        parsed = _parse_named_callback(query.data)
        if parsed is None:
            await query.answer("Unknown button.", show_alert=True)
            return
        await query.answer()
        await self._edit_lanes(query, await self._run_named(*parsed))

    async def _reply_named(self, update: Update, usage: str, parsed) -> None:
        if not self.site.is_owner(update.effective_user.id):
            await update.message.reply_text(LANES_DENIED)
            return
        if parsed is None:
            await update.message.reply_text(f"Usage: {usage}")
            return
        verb, k, name = parsed
        if not lane_name_ok(name):
            await update.message.reply_text(LANE_NAME_RULE)
            return
        question, keyboard = _named_confirm(verb, k, name)
        await update.message.reply_text(question, reply_markup=keyboard)

    async def cmd_laneadd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Add one CLI lane, optionally copying lane K's settings, after a confirm (owner only)."""
        parsed = _parse_laneadd(context.args)
        await self._reply_named(update, "/laneadd NAME [from K]",
                                ("laneadd", *parsed) if parsed else None)

    async def cmd_lanerename(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Rename one CLI lane after a confirm; it keeps betting (owner only)."""
        args = list(context.args or [])
        ok = len(args) >= 2 and _LANE_K.match(args[0])
        await self._reply_named(update, "/lanerename K NAME",
                                ("lanern", int(args[0]), " ".join(args[1:])) if ok else None)

    async def handle_callback(self, query) -> bool:
        """Answer a lane or session button; False when the data is not one of these buttons."""
        data = query.data or ""
        if data == "lanes_refresh":
            await self._lanes_refresh(query)
        elif data.startswith(("sess:", "sessd:")):
            await self._sessions_callback(query)
        elif data.startswith(("lanepick:", "laneuse:", "laneuse_end:")):
            await self._account_callback(query)
        elif data.startswith(("laneadd:", "lanern:")):
            await self._named_lane_callback(query)
        elif data.startswith(("lane:", "lanego:")):
            await self._lane_callback(query)
        else:
            return False
        return True
