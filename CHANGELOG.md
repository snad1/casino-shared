# Changelog

## v0.3.0 — Lane split + run-wide profit increment helpers (2026-11-15)

Pure helpers for WolfBet CLI v2.36.0 (lane money split) and v2.37.0 (run-wide profit increment, `0b7e71a`), used by the targets' E9 and E10 work. Existing functions keep their signatures and behaviour.

### Added

- **core/lanes.py** — `split_base_bet(base, lanes, mode, has_override, min_bet=0) -> (bet, note|None)`: in `bet` / `both` divides the base bet (or a `basebet_pct`-derived bet) by the lane count. A lane's own `base_bet` override is never divided, and one lane is a no-op. A result that rounds to 0 or falls below the service's minimum bet stays full, with a note for the caller to log. `lane_config` now uses it, with the same output as before, and takes an optional `min_bet` (default 0 = no minimum) that it passes on, so a service's split base bet below its minimum stays full.
- **core/run_increment.py** (new) — `PROFIT_INCREMENT_SCOPES`, `OFF_VALUES`, `parse_scope`, `increment_on`, `run_path` (per lane via `lane_path`), `load_run` (missing / corrupt file reads as `None`), `save_run` (atomic tmp + `os.replace`, mode `0o600`, raises `OSError` for the caller to log), `run_profit_done(conn, first_sid, exclude_sid=None, lane=None)`, `db_max_session_id(conn, lane=None)`, `run_is_stale`, `resume_base_bet`, `next_milestone`, `check_profit_increment` (the merged WolfBet `_check_profit_increment` rule: one bump per check)

### Unchanged on purpose

- `lane_split(cfg)` still reads an unknown mode as `none`, as WolfBet's `_lane_split` does. An unknown `lane_split=` value is still refused by `split_lane_pairs`, with WolfBet's text.
- `split_budget_on(lanes, mode)` and `split_bet_divisor(lanes, mode)` keep their argument order, because all five targets already call them.

### Not ported (N/A)

- The auto-bet UUID restart inside WolfBet's `_check_profit_increment`, because no target has a server-side auto-bet. The bumped base applies to the next bet.

### Testing

- **362 tests** (299 → 362): `split_base_bet` cases in `test_core_lanes.py`, and new `test_core_run_increment.py`, adapted from WolfBet's `test_phase10_lanes.py` and `test_phase10b_run_increment.py`

---

## v0.2.0 — WolfBet sync helpers (2026-11-13)

Pure, API-independent helpers ported from WolfBet `6174b96` (CLI v2.36.0) so the five target bots can share them. WolfBet keeps its own copies. Bet loops and API clients stay in each service.

### Added

- **core/session_rules.py** — restart and streak logic: `record_outcome`, `streak_reset_triggered`, `should_restart`, `restart_delay_for`, `clamp` (`RECURRING_DELAY_MAX` = 86400, `STREAK_RESET_DELAY_MIN` = 5), `parse_streak_delay`, `streak_delay_for`, `streakbet_loss_factor`, `martingale_chain_depth`, `STREAK_RESET_REASON` / `streak_reset_stop_reason`
- **core/stats.py** — stats windows and durations: `normalize_window_bound`, `stats_date_window(since, until, lane=None)` (space-separated times no longer match the whole day), `fmt_duration`, `sessions_time_span`
- **core/diagnostics.py** — `percentile`, `benchmark_verdict(..., casino_name=)` (casino name passed in, not hard-coded)
- **core/net.py** — `validate_proxy`, `mask_proxy`
- **core/lanes.py** — parallel-lane helpers (WolfBet v2.35.0 lanes + v2.36.0 `lane_split`): `lane_path`, `lanes_count`, `lane_overrides`, `lane_config`, `split_lane_pairs`, `check_lane_keys`, `validate_lane`, `lane_split`, `split_budget_on`, `split_bet_divisor`, `lane_share`, `lane_equity`, `lane_budget_exceeded`, `recommend_lanes`
- **core/database.py** — `SESSIONS_SCHEMA` gains `lane INTEGER DEFAULT 1`; idempotent `ensure_sessions_lane(conn)` for existing DBs (older rows read as lane 1)

### Not ported (N/A)

- `classify_auto_stop_reason`, `soft_pace_delay`, `_autostart_global_pace` — depend on WolfBet's server-side auto-bet and `x-ratelimit-*` headers, which no target API has

### Testing

- **299 tests** (114 → 299), cases adapted from WolfBet's phase tests

---

## v0.1.0 — Initial release + pip package (2026-03-29)

### Added

- **core/database.py** — `SESSIONS_SCHEMA`, `cleanup_old_bets`, `db_connect`, `secure_path`
- **core/strategy.py** — `StrategyRule`, `describe_rule`, `cmp`, `load_rules_from_text`, `RULE_ACTIONS`, `SEQ_MODES`, `make_lookups`
- **tg/database.py** — Per-user file paths (`user_dir`, `user_db_path`, `user_config_path`, `user_presets_path`) and config/preset persistence with `0o600` permissions
- **web/auth.py** — `make_auth()` factory returning JWT create/decode/extract/role-check functions
- **web/bot_db.py** — Read-only per-user bot DB access: user discovery, sessions, bets, chart data, platform stats
- **web/database.py** — Admin users (PBKDF2 260K iterations), TG users, web sessions, audit log with SQL injection prevention via column allowlists
- **web/services.py** — Systemctl management, git pull, safe file-copy update, system metrics, `make_services()` factory
- **web/websocket.py** — `ConnectionManager` with channel-based pub/sub, `make_authenticate_ws()` factory
- **web/routes/auth_routes.py** — `make_auth_router()` factory for login/logout/Telegram auth routes

### Security

- PBKDF2-HMAC-SHA256 with 260,000 iterations + `hmac.compare_digest` for timing-safe password verification
- All file writes use `os.open()` with `0o600` permissions
- `_ALLOWED_TG_COLUMNS` allowlist prevents SQL injection in dynamic UPDATE queries
- `shutil.copy2` used instead of `create_subprocess_shell` to prevent shell injection
- `datetime.now(timezone.utc)` used throughout (no deprecated `utcnow()`)
- Specific exception types (`json.JSONDecodeError`, `OSError`, `ValueError`) instead of broad `except Exception`

### Testing

- **114 tests** covering all modules: database, auth, strategy, services, bot_db, tg persistence
- Tests run with `APP_ENV=testing` using temporary databases and directories

### Infrastructure

- **pyproject.toml** — Added proper package config for `pip install` from GitHub
- **Build backend** — Fixed `setuptools.build_meta` (was broken `setuptools.backends._legacy`)
- **Dependencies** — Added `jinja2` to package dependencies (required by `web.routes.auth_routes`)
- **.gitignore** — Added `*.egg-info/`, `build/`, `dist/` for build artifacts
