"""Tests for shared/core/database.py — DB utilities."""

import os
import sqlite3

import pytest

from shared.core.database import (
    SESSIONS_SCHEMA, cleanup_old_bets, db_connect, secure_path,
)


def _init_test_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(SESSIONS_SCHEMA)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            timestamp TEXT,
            amount REAL,
            state TEXT,
            profit REAL,
            balance_after REAL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    """)
    conn.commit()
    return conn


class TestSessionsSchema:
    def test_creates_sessions_table(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        conn.execute(SESSIONS_SCHEMA)
        conn.commit()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
        assert "id" in cols
        assert "started_at" in cols
        assert "profit" in cols
        assert "peak_bps" in cols
        assert "low_bpm" in cols
        conn.close()

    def test_idempotent(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        conn.execute(SESSIONS_SCHEMA)
        conn.execute(SESSIONS_SCHEMA)
        conn.commit()
        conn.close()


class TestDbConnect:
    def test_returns_connection(self, tmp_db):
        _init_test_db(tmp_db).close()
        conn = db_connect(tmp_db)
        assert conn is not None
        conn.close()

    def test_wal_mode(self, tmp_db):
        _init_test_db(tmp_db).close()
        conn = db_connect(tmp_db)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()


class TestSecurePath:
    def test_sets_600_permissions(self, tmp_path):
        f = tmp_path / "secret.txt"
        f.write_text("sensitive")
        secure_path(str(f))
        perms = oct(os.stat(str(f)).st_mode)[-3:]
        assert perms == "600"

    def test_nonexistent_file_no_error(self):
        secure_path("/nonexistent/path/file.txt")


class TestCleanupOldBets:
    def test_deletes_old_bets(self, tmp_db):
        conn = _init_test_db(tmp_db)
        conn.execute(
            "INSERT INTO sessions (id, started_at, ended_at) VALUES (?, ?, ?)",
            (1, "2020-01-01T00:00:00", "2020-01-01T01:00:00"),
        )
        conn.execute(
            "INSERT INTO bets (session_id, timestamp, amount, state, profit, balance_after) VALUES (?, ?, ?, ?, ?, ?)",
            (1, "2020-01-01T00:30:00", 0.001, "win", 0.001, 1.0),
        )
        conn.commit()
        conn.close()

        deleted = cleanup_old_bets(tmp_db, bet_age_days=1)
        assert deleted == 1

        conn = sqlite3.connect(tmp_db)
        count = conn.execute("SELECT COUNT(*) FROM bets").fetchone()[0]
        assert count == 0
        purged = conn.execute("SELECT bets_purged FROM sessions WHERE id=1").fetchone()[0]
        assert purged == 1
        conn.close()

    def test_keeps_recent_bets(self, tmp_db):
        conn = _init_test_db(tmp_db)
        conn.execute(
            "INSERT INTO sessions (id, started_at, ended_at) VALUES (?, ?, datetime('now'))",
            (1, "2025-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO bets (session_id, timestamp, amount, state, profit, balance_after) VALUES (?, ?, ?, ?, ?, ?)",
            (1, "2025-01-01T00:30:00", 0.001, "win", 0.001, 1.0),
        )
        conn.commit()
        conn.close()

        deleted = cleanup_old_bets(tmp_db, bet_age_days=1)
        assert deleted == 0

    def test_keeps_active_session_bets(self, tmp_db):
        conn = _init_test_db(tmp_db)
        conn.execute(
            "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
            (1, "2020-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO bets (session_id, timestamp, amount, state, profit, balance_after) VALUES (?, ?, ?, ?, ?, ?)",
            (1, "2020-01-01T00:30:00", 0.001, "win", 0.001, 1.0),
        )
        conn.commit()
        conn.close()

        deleted = cleanup_old_bets(tmp_db, bet_age_days=1)
        assert deleted == 0

    def test_adds_bets_purged_column(self, tmp_db):
        conn = _init_test_db(tmp_db)
        conn.commit()
        conn.close()
        cleanup_old_bets(tmp_db)
        conn = sqlite3.connect(tmp_db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
        assert "bets_purged" in cols
        conn.close()
