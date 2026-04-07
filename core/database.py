"""Common database utilities — connection, cleanup, security.

Each bot's core/database.py imports these and defines its own init_db
with bot-specific bets table schema.
"""

import os
import sqlite3


SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT,
    ended_at      TEXT,
    currency      TEXT,
    game          TEXT,
    strategy      TEXT,
    base_bet      REAL,
    multiplier    REAL,
    total_bets    INTEGER DEFAULT 0,
    wins          INTEGER DEFAULT 0,
    losses        INTEGER DEFAULT 0,
    profit        REAL    DEFAULT 0,
    wagered       REAL    DEFAULT 0,
    start_balance REAL,
    end_balance   REAL,
    max_win_streak  INTEGER DEFAULT 0,
    max_loss_streak INTEGER DEFAULT 0,
    highest_balance REAL DEFAULT 0,
    lowest_balance  REAL DEFAULT 0,
    highest_win     REAL DEFAULT 0,
    biggest_loss    REAL DEFAULT 0,
    bets_per_minute REAL DEFAULT 0,
    bets_per_second REAL DEFAULT 0,
    peak_bps        REAL DEFAULT 0,
    low_bps         REAL DEFAULT 0,
    peak_bpm        REAL DEFAULT 0,
    low_bpm         REAL DEFAULT 0
)
"""


def cleanup_old_bets(db_path: str, bet_age_days: int = 3):
    """Delete bet rows for ended sessions older than bet_age_days.

    Session rows (with all pre-aggregated stats) are kept forever.
    Only the individual bet detail rows are purged to reclaim disk space.
    """
    conn = sqlite3.connect(db_path, timeout=10)
    c = conn.cursor()

    try:
        c.execute("ALTER TABLE sessions ADD COLUMN bets_purged INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    c.execute("""
        DELETE FROM bets
        WHERE session_id IN (
            SELECT id FROM sessions
            WHERE ended_at IS NOT NULL
              AND ended_at < datetime('now', ?)
              AND bets_purged = 0
        )
    """, (f'-{bet_age_days} days',))

    deleted = c.rowcount

    if deleted > 0:
        c.execute("""
            UPDATE sessions SET bets_purged = 1
            WHERE ended_at IS NOT NULL
              AND ended_at < datetime('now', ?)
              AND bets_purged = 0
        """, (f'-{bet_age_days} days',))

    conn.commit()

    if deleted > 0:
        conn.execute("VACUUM")

    conn.close()
    return deleted


def cleanup_live_bets(db_path: str, bet_age_days: int = 1):
    """Delete old bet rows from ALL sessions (including running ones).

    Keeps bets newer than bet_age_days. Session stats are unaffected
    because they're aggregated in the sessions table, not derived from bets.
    Call periodically during long-running sessions to prevent DB bloat.
    """
    conn = sqlite3.connect(db_path, timeout=10)
    deleted = conn.execute("""
        DELETE FROM bets
        WHERE REPLACE(timestamp, 'T', ' ') < datetime('now', ?)
    """, (f'-{bet_age_days} days',)).rowcount
    conn.commit()
    if deleted > 1000:
        conn.execute("VACUUM")
    conn.close()
    return deleted


def db_connect(db_path: str):
    """Create a WAL-mode connection to the database."""
    conn = sqlite3.connect(db_path, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def secure_path(path: str):
    """Set restrictive file permissions (owner read/write only)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
