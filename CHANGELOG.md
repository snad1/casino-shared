# Changelog

## v0.11.0 - lane accounts and secret checks (2026-11-25)

- `core/lanes.py`: `lane_account(cfg, lane, forbidden, default_key)` gives the key, currency, proxy and vault proxy a lane bets with; `account_lanes(...)` counts the enabled lanes on the same key and currency. `lane_config` now divides the bet split by `account_lanes`, so lanes on different accounts never share a split. For a bot where key and currency can't differ per lane the result is the same as before. Moved from WolfBet.
- `core/vault.py`: `check_secret(secret, kind)` refuses a key that isn't 8-4096 printable characters, a Telegram token of the wrong shape and a proxy that isn't http, https or socks5; the message never repeats the secret. `KEY_SECRET_RE`, `KEY_SECRET_HELP` and `TELEGRAM_TOKEN_RE` moved from WolfBet.

**Tests**: `tests/test_core_lane_registry.py` (accounts, split per account, the count shape unchanged), `tests/test_core_vault.py` (secret checks without echo).

## v0.10.0 - lane registry writers (2026-11-24)

- `core/lanes.py` writes the lane registry too: `split_registry_pairs` (config-set pairs for `lanes`, `lanes_max`, `lane_split`, `laneK.name` and `laneK.key`, lane ids up to 99), `next_lane_id` (never a removed id), `set_enabled_count` (disabled lanes come back first, then new ids) and `registry_block` (the `lanes` block to write). Moved from WolfBet so each bot port uses one copy. `split_lane_pairs` is unchanged for the bots still on the count shape.

**Tests**: `tests/test_core_lane_registry.py` (pair splitting and refusals, enabling counts up and down, ids running out, the written block reads back).

## v0.9.0 - lane registry, session-end requests and a vault client (2026-11-24)

- `core/lanes.py` reads the lane registry: `lane_registry`, `lane_ids`, `lane_name`, `default_lane_name`, `lanes_max`, `lane_file_ids`, `LANE_ID_MAX`, `LANE_NAME_RE` and `LANE_NAME_HELP`. Named lanes (`lanes.items`) and the older `lanes.count` + `lanes.overrides` shape read the same way. `lanes_count` and `lane_overrides` now read the registry; for a config with `lanes.count` they return what they always did.
- Session-end requests: `request_drain(path, lane, action)` writes `restart` or `remove` for a lane (0600) or forgets it with `cancel`; `take_drain(path, lane)` reads it once and deletes it.
- Vault client: `vault_secret(service, key_id, kind)` and `vault_info(service, key_id)` give None when an entry is absent, of another kind, a bad id, or the vault is unusable; the last is logged on the caller's logger, never with the secret.

**Tests**: `tests/test_core_lane_registry.py` (both config shapes, clamps, lane files, requests), `tests/test_core_vault.py` (the client). The goated, shuffle, stake, cryptogames and duckdice suites pass unchanged.

## v0.8.0 - a vault entry can hold the Telegram bot token (2026-11-24)

- New kind `telegram`: `Vault.put(..., kind="telegram")` stores a Telegram bot token like any other secret (encrypted, write-only, listed with its last 4 characters).

**Tests**: `tests/test_core_vault.py` (a telegram entry is stored, listed with its kind and read back).

## v0.7.0 - vault entries have a kind (2026-11-24)

- `Vault.put(..., kind="key")` stores what an entry is: a casino `key` or a `proxy` URL that carries credentials. `KeyInfo.kind` reports it; files written before v0.7.0 read as `key`. Any other kind raises `VaultError`.

**Tests**: `tests/test_core_vault.py` (kind stored and listed, older files read as keys, unknown kind refused).

## v0.6.0 - list and delete vault keys (2026-11-23)

- `Vault.keys()` returns the `KeyInfo` of every key that opens, sorted by id; a damaged, unreadable or loosely permissioned file is left out instead of failing the whole list. No secret is decrypted.
- `Vault.delete(key_id)` removes one key and returns False when there was none.

**Tests**: `tests/test_core_vault.py` (list order and owner, a damaged file skipped, an empty vault, delete).

## v0.5.0 - host key vault (2026-11-23)

`shared.core.vault` keeps casino API keys encrypted on the bot host, one file per key, so a config file never has to hold one in plaintext.

- `Vault(service, root=None, kek_path=None)`: keys live in `<root>/<service>/<key_id>.json` (default root `~/.casino-vault`, or `CASINO_VAULT_DIR`), sealed with AES-256-GCM under a 32-byte key in `kek_path` (default `~/.config/casino-agent/vault.key`, or `CASINO_VAULT_KEY_FILE`), created on the first write. Files are 0600 and directories 0700.
- `put(key_id, secret, label, owner_user_id=None)` stores or replaces a key (a replace keeps `created_at` and sets `rotated_at`); `secret(key_id)` decrypts it; `info(key_id)` returns `KeyInfo` (id, label, owner, last 4 characters, times) without decrypting. A missing key is `None`.
- Each file carries its service and id as associated data, so a file copied under another id does not open. A wrong or missing vault key, a damaged file, a file other users can read or one owned by someone else raises `VaultError`, whose message names the key id and never the secret.
- `cryptography>=41` is now a direct dependency (it was already installed through `python-jose[cryptography]`).

**Tests**: `tests/test_core_vault.py` (round trip, private modes, rotation times, wrong key, a file under another id, tampering, loose permissions, missing key file, bad ids and service names, environment paths). The suite's `conftest.py` points the vault at a temp dir.

## v0.4.0 - event publisher for the dashboard (2026-11-23)

`shared.core.events` streams a bot's bets, state and lifecycle events to Redis, so each bot can feed the live dashboard from one module instead of its own copy. It is the WolfBet publisher with the bot-specific parts made arguments.

- `EventPublisher(url, *, stream_prefix, bet_fields, lane=1, svc=None, maxlen=None, queue_max=50_000, client=None, scoped=False)`. `stream_prefix` names the streams (`<prefix>:bets`, `:state`, `:lifecycle`) and `bet_fields` the order of the values `emit_bet` takes; both are required. `svc` (default: the prefix) goes into every envelope.
- Wire format v1 unchanged: one `data` field holding `{v, svc, lane, pid, seq, ts_ms, type, payload}`.
- The bet path stays O(1): `emit_*` append flat frames to a bounded deque; one daemon thread sends pipelined XADD batches every 100 ms. Redis down, slow or not installed never blocks or raises; losses are counted (`dropped`, `overflow`) and show up as seq gaps. `reserve()` lets a caller skip building an event the full queue would drop.
- `scoped=True` serves many users from one process (`<prefix>:<uid>:<stream>`, seq per user).
- `validate_events_url(url)` accepts `redis://`, `rediss://` and `unix://` only. The URL is never logged.
- `redis>=5` is an optional extra (`casino-shared[events]`), imported only when a publisher starts.

**Tests**: `tests/test_core_events.py` (26).

## v0.3.5 - bumps belong to the base they were earned on (2026-11-23)

`shared.core.run_increment` keeps the base-bet rule from WolfBet CLI v2.37.2 in one place for the five CLIs.

- The run file can carry `base`, the configured base the bumps were earned on. `load_run` returns it (`None` for older files); `save_run` writes it only when set, so callers that do not track it (the Telegram engines) write the same file as before.
- `bumps_for_base(bumps, origin, config_base)` returns 0 when the bumps were earned on another base, and keeps them when the file has no origin.
- `rebaseline(profit_total, threshold)` is the next milestone after bumps are dropped: one step above the run's current profit, the first step for a negative profit, 0.0 when the threshold is off.
- `resume_base_bet` is unchanged.

**Tests**: `tests/test_core_run_increment.py` (+11).

## v0.3.4 - secret redaction for log output (2026-11-15)

`shared.tg.redact` gives the Telegram bots one way to keep API keys out of journald.

- `register_secret(value)` / `unregister_secret(value)` track the live keys of every running engine in the process.
- `redact(text)` masks registered keys wherever they appear, `api_key=` query values, `Bearer` tokens and JWTs; `mask_key` keeps the first 8 and last 4 characters.
- `RedactingFormatter(fmt, datefmt=None)` is a drop-in `logging.Formatter` that runs each finished record through `redact`.
- The registry exists because cryptogames puts the key in the URL path, so no pattern can find it without knowing the key.

**Tests**: `tests/test_tg_redact.py` (9). **421 total passing** plus these.

## v0.3.3 - finite-number and web-credential validation for every bot (2026-11-15)

Two helpers the ported bots were each missing.

### `shared.core.validation`
- `finite_float(name, value, minimum=None, maximum=None)` rejects `nan`, `inf`, non-numeric input and out-of-range values, and returns the number. Messages name the field only, so they are safe to show a Telegram user.
- `InvalidNumber` is a `ValueError`, so existing `except ValueError` paths keep working.
- Why it exists: `float("nan") < 0` is False, so every `< 0` guard in the bots accepted a `nan` stop-loss and the comparison that should stop betting never fired.

### `shared.web.settings`
- `validate_web_credentials(secret_key, admin_pass, env_prefix="")` refuses the shipped secret and password, a secret under 32 bytes and a password under 12 characters, naming the bot's own variables (for example `GOATED_WEB_SECRET_KEY`).
- `DEFAULT_SECRET_KEY`, `DEFAULT_ADMIN_PASS`, `MIN_SECRET_BYTES`, `MIN_ADMIN_PASS_CHARS` are exported so a bot can keep its field defaults without repeating the literals.

**Tests**: `tests/test_core_validation.py` (16), `tests/test_web_settings.py` (7). **421 total passing.**

## v0.3.2 - web auth and token hardening (2026-11-15)

All six bots import `shared.web`, so this lands once for all of them. A bot opts into the lockout and one-time-link controls by passing the new callbacks to `make_auth_router`; without them its login keeps today's behaviour.

### Authorization
- `make_auth` now returns `require_user_access(user_id, request)`: admins pass, a Telegram user passes only when the token subject is `tg:<user_id>`, everything else is 403. The path parameter is resolved by FastAPI, so it drops into any `/api/users/{user_id}/...` route as `Depends(require_user_access)`.

### Tokens
- `decode_token` rejects a token with no `jti` whenever the session check is on. The Telegram link exchange calls it with the check off, so a link token still works, once.
- `get_token_from_request` reads the cookie, then `Authorization: Bearer`; the `?token=` query parameter is no longer accepted. Query strings land in access logs and `Referer`.
- `make_authenticate_ws` authenticates from the cookie only and refuses a handshake whose `Origin` host differs from the `Host` header. A missing `Origin` is allowed; browsers always send one on a WebSocket.

### Login, logout, links
- `POST /logout` with a double-submit `csrf_token` (cookie plus form field, compared with `hmac.compare_digest`). `GET /logout` answers 405.
- Login sets a `csrf_token` cookie; both session cookies are `Secure` when `settings.app_env == "production"`, instead of depending on the request scheme behind the proxy.
- `/auth/telegram` consumes the link's `jti` before issuing a session, so a 5-minute link opens exactly one.

### Lockout
- `login_locked_until`, `record_login_failure`, `clear_login_failures`: five failures for the same first forwarded hop and username lock that pair for 15 minutes, in the web database so it survives restarts and both workers. `X-Forwarded-For` is used because nginx is the only listener.

### Passwords
- PBKDF2 260k -> 600k, hash format `pbkdf2:sha256:<iters>:<salt>:<hash>`. Legacy three-field hashes still verify and `verify_admin` rehashes them on a successful login.
- `used_login_tokens` and `login_failures` tables are created with `IF NOT EXISTS` on every `init_db`, so an existing install needs no migration.
- `python-multipart` is now a declared dependency; the login form always required it.

**Tests**: `tests/test_web_auth.py` (26), `tests/test_web_auth_routes.py` (8, new), `tests/test_web_database.py` (39). **395 total passing.**

## v0.3.1 — A stored shipped admin password is replaced at startup (2026-11-15)

`init_db` seeded the admin row with `INSERT OR IGNORE`, so a bot installed before web credentials were hardened kept the shipped `changeme123` hash forever: a strong password in `.env` passed the startup check while the old password still logged in. `init_db` now replaces that stored hash with the configured password once, and only while the stored hash still matches the shipped default. A password the operator already chose is never touched.

**Tests:** `tests/test_web_database.py` (3).

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
