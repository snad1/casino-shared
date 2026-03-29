# Changelog

## v1.0.0 — Initial Release (2026-03-29)

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
