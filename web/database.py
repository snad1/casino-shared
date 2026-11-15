"""Web app's own SQLite database: admin users, sessions, audit log."""

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
import hashlib
import hmac

_SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT DEFAULT 'admin',
    created_at    TEXT DEFAULT (datetime('now')),
    last_login    TEXT
);

CREATE TABLE IF NOT EXISTS tg_users (
    user_id       INTEGER PRIMARY KEY,
    username      TEXT,
    tier          TEXT DEFAULT 'free',
    first_seen    TEXT DEFAULT (datetime('now')),
    last_active   TEXT,
    is_blocked    INTEGER DEFAULT 0,
    notes         TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS web_sessions (
    id            TEXT PRIMARY KEY,
    user_type     TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    created_at    TEXT DEFAULT (datetime('now')),
    expires_at    TEXT NOT NULL,
    revoked       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT DEFAULT (datetime('now')),
    actor         TEXT NOT NULL,
    action        TEXT NOT NULL,
    detail        TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS used_login_tokens (
    jti           TEXT PRIMARY KEY,
    used_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS login_failures (
    key           TEXT PRIMARY KEY,
    count         INTEGER NOT NULL DEFAULT 0,
    first_at      TEXT NOT NULL,
    locked_until  TEXT
);
"""

_ALLOWED_TG_COLUMNS = {"username", "tier", "last_active", "is_blocked", "notes"}

# The password the installer shipped before credentials were hardened. It is
# only used to recognise (and replace) a hash an older install already stored.
DEFAULT_ADMIN_PASS = "changeme123"

_ITERATIONS = 600_000
_LEGACY_ITERATIONS = 260_000


def _hash_password(password: str) -> str:
    salt = uuid.uuid4().hex
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS)
    return f"pbkdf2:sha256:{_ITERATIONS}:{salt}:{h.hex()}"


def _parse_hash(stored: str) -> tuple[int, str, str] | None:
    """Return (iterations, salt, hash) for a current or legacy stored hash."""
    parts = stored.split(":")
    try:
        if len(parts) == 5 and parts[0] == "pbkdf2" and parts[1] == "sha256":
            return int(parts[2]), parts[3], parts[4]
        if len(parts) == 3 and parts[0] == "pbkdf2":
            return _LEGACY_ITERATIONS, parts[1], parts[2]
    except ValueError:
        return None
    return None


def _verify_password(password: str, stored: str) -> bool:
    parsed = _parse_hash(stored)
    if parsed is None:
        return False
    iterations, salt, hash_hex = parsed
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return hmac.compare_digest(h.hex(), hash_hex)


def _needs_rehash(stored: str) -> bool:
    parsed = _parse_hash(stored)
    return parsed is None or parsed[0] < _ITERATIONS


def get_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str, admin_user: str, admin_pass: str):
    conn = get_db(db_path)
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO admin_users (username, password_hash, role) VALUES (?, ?, ?)",
        (admin_user, _hash_password(admin_pass), "admin"),
    )
    # The insert above is ignored once the row exists, so an install that
    # predates credential hardening would keep the shipped password forever.
    # Replace that stored hash only while it still matches the shipped default;
    # a password the operator chose is never overwritten.
    row = conn.execute(
        "SELECT id, password_hash FROM admin_users WHERE username = ?", (admin_user,)
    ).fetchone()
    if (
        row
        and admin_pass != DEFAULT_ADMIN_PASS
        and _verify_password(DEFAULT_ADMIN_PASS, row["password_hash"])
    ):
        conn.execute(
            "UPDATE admin_users SET password_hash = ? WHERE id = ?",
            (_hash_password(admin_pass), row["id"]),
        )
    conn.commit()
    conn.close()


def verify_admin(db_path: str, username: str, password: str) -> dict | None:
    conn = get_db(db_path)
    row = conn.execute(
        "SELECT * FROM admin_users WHERE username = ?", (username,)
    ).fetchone()
    if row and _verify_password(password, row["password_hash"]):
        updates = ["last_login = ?"]
        params = [datetime.now(timezone.utc).isoformat()]
        if _needs_rehash(row["password_hash"]):
            updates.append("password_hash = ?")
            params.append(_hash_password(password))
        params.append(row["id"])
        conn.execute(f"UPDATE admin_users SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        conn.close()
        return dict(row)
    conn.close()
    return None


def change_admin_password(db_path: str, user_id: int, old_password: str, new_password: str) -> bool:
    conn = get_db(db_path)
    row = conn.execute(
        "SELECT * FROM admin_users WHERE id = ?", (user_id,)
    ).fetchone()
    if not row or not _verify_password(old_password, row["password_hash"]):
        conn.close()
        return False
    conn.execute(
        "UPDATE admin_users SET password_hash = ? WHERE id = ?",
        (_hash_password(new_password), row["id"]),
    )
    conn.commit()
    conn.close()
    return True


def sync_tg_user(db_path: str, user_id: int, username: str = None, tier: str = None):
    conn = get_db(db_path)
    existing = conn.execute(
        "SELECT user_id FROM tg_users WHERE user_id = ?", (user_id,)
    ).fetchone()
    now = datetime.now(timezone.utc).isoformat()
    if existing:
        updates = ["last_active = ?"]
        params = [now]
        if username:
            updates.append("username = ?")
            params.append(username)
        if tier:
            updates.append("tier = ?")
            params.append(tier)
        params.append(user_id)
        conn.execute(
            f"UPDATE tg_users SET {', '.join(updates)} WHERE user_id = ?",
            params,
        )
    else:
        conn.execute(
            "INSERT INTO tg_users (user_id, username, tier, first_seen, last_active) VALUES (?, ?, ?, ?, ?)",
            (user_id, username or "", tier or "free", now, now),
        )
    conn.commit()
    conn.close()


def get_tg_users(db_path: str) -> list[dict]:
    conn = get_db(db_path)
    rows = conn.execute(
        "SELECT * FROM tg_users ORDER BY last_active DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_tg_user(db_path: str, user_id: int) -> dict | None:
    conn = get_db(db_path)
    row = conn.execute(
        "SELECT * FROM tg_users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_tg_user(db_path: str, user_id: int, **kwargs):
    safe = {k: v for k, v in kwargs.items() if k in _ALLOWED_TG_COLUMNS}
    if not safe:
        return
    conn = get_db(db_path)
    sets = [f"{k} = ?" for k in safe]
    vals = list(safe.values()) + [user_id]
    conn.execute(f"UPDATE tg_users SET {', '.join(sets)} WHERE user_id = ?", vals)
    conn.commit()
    conn.close()


def audit(db_path: str, actor: str, action: str, detail: str = ""):
    conn = get_db(db_path)
    conn.execute(
        "INSERT INTO audit_log (actor, action, detail) VALUES (?, ?, ?)",
        (actor, action, detail),
    )
    conn.commit()
    conn.close()


def get_audit_log(db_path: str, limit: int = 100) -> list[dict]:
    conn = get_db(db_path)
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_web_session(db_path: str, user_type: str, user_id: str, expires_at: str) -> str:
    jti = str(uuid.uuid4())
    conn = get_db(db_path)
    conn.execute(
        "INSERT INTO web_sessions (id, user_type, user_id, expires_at) VALUES (?, ?, ?, ?)",
        (jti, user_type, user_id, expires_at),
    )
    conn.commit()
    conn.close()
    return jti


def is_session_valid(db_path: str, jti: str) -> bool:
    conn = get_db(db_path)
    row = conn.execute(
        "SELECT revoked FROM web_sessions WHERE id = ?", (jti,)
    ).fetchone()
    conn.close()
    return row is not None and not row["revoked"]


def revoke_session(db_path: str, jti: str):
    conn = get_db(db_path)
    conn.execute("UPDATE web_sessions SET revoked = 1 WHERE id = ?", (jti,))
    conn.commit()
    conn.close()


def consume_login_token(db_path: str, jti: str) -> bool:
    """Record a one-time login token; False when it was already exchanged."""
    if not jti:
        return False
    conn = get_db(db_path)
    cursor = conn.execute("INSERT OR IGNORE INTO used_login_tokens (jti) VALUES (?)", (jti,))
    conn.commit()
    consumed = cursor.rowcount == 1
    conn.close()
    return consumed


def login_locked_until(db_path: str, key: str) -> str | None:
    """ISO time the key stays locked, or None when it may try again."""
    conn = get_db(db_path)
    row = conn.execute(
        "SELECT locked_until FROM login_failures WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    if not row or not row["locked_until"]:
        return None
    if row["locked_until"] <= datetime.now(timezone.utc).isoformat():
        return None
    return row["locked_until"]


def record_login_failure(db_path: str, key: str, max_attempts: int = 5,
                         window_minutes: int = 15, lockout_minutes: int = 15) -> str | None:
    """Count one failed attempt; return the lockout expiry once it is reached."""
    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(minutes=window_minutes)).isoformat()
    conn = get_db(db_path)
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        "SELECT count, first_at FROM login_failures WHERE key = ?", (key,)
    ).fetchone()
    if row is None or row["first_at"] < window_start:
        count = 1
        first_at = now.isoformat()
    else:
        count = row["count"] + 1
        first_at = row["first_at"]
    locked_until = (
        (now + timedelta(minutes=lockout_minutes)).isoformat()
        if count >= max_attempts
        else None
    )
    conn.execute(
        "INSERT INTO login_failures (key, count, first_at, locked_until) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET count = excluded.count, "
        "first_at = excluded.first_at, locked_until = excluded.locked_until",
        (key, count, first_at, locked_until),
    )
    conn.commit()
    conn.close()
    return locked_until


def clear_login_failures(db_path: str, key: str):
    conn = get_db(db_path)
    conn.execute("DELETE FROM login_failures WHERE key = ?", (key,))
    conn.commit()
    conn.close()
