# Changelog

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
