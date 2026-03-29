"""Tests for shared/web/bot_db.py — per-user bot database access."""

import json
import os
import sqlite3

import pytest

from shared.web.bot_db import (
    discover_users, get_user_config, save_user_config,
    get_sessions, get_session_count, get_session, get_session_stats,
    get_bets, get_bet_count, get_bets_for_chart,
    get_platform_stats,
)

DB_FILENAME = "test.db"
CHART_COLS = "id, timestamp, amount, state, profit, balance_after"


def _create_user_db(data_dir, user_id):
    user_path = os.path.join(data_dir, str(user_id))
    os.makedirs(user_path, exist_ok=True)
    db_path = os.path.join(user_path, DB_FILENAME)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY, started_at TEXT, ended_at TEXT,
            currency TEXT, game TEXT, strategy TEXT, base_bet REAL,
            multiplier REAL, total_bets INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
            profit REAL DEFAULT 0, wagered REAL DEFAULT 0,
            start_balance REAL, end_balance REAL,
            max_win_streak INTEGER DEFAULT 0, max_loss_streak INTEGER DEFAULT 0,
            highest_balance REAL DEFAULT 0, lowest_balance REAL DEFAULT 0,
            highest_win REAL DEFAULT 0, biggest_loss REAL DEFAULT 0,
            bets_per_minute REAL DEFAULT 0, bets_per_second REAL DEFAULT 0,
            peak_bps REAL DEFAULT 0, low_bps REAL DEFAULT 0,
            peak_bpm REAL DEFAULT 0, low_bpm REAL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE bets (
            id INTEGER PRIMARY KEY, session_id INTEGER, timestamp TEXT,
            amount REAL, state TEXT, profit REAL, balance_after REAL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    """)
    conn.commit()
    return conn


class TestDiscoverUsers:
    def test_finds_users(self, tmp_data_dir):
        _create_user_db(tmp_data_dir, 111).close()
        _create_user_db(tmp_data_dir, 222).close()
        users = discover_users(tmp_data_dir, DB_FILENAME)
        assert users == [111, 222]

    def test_empty_dir(self, tmp_data_dir):
        assert discover_users(tmp_data_dir, DB_FILENAME) == []

    def test_nonexistent_dir(self):
        assert discover_users("/nonexistent/path", DB_FILENAME) == []

    def test_skips_non_numeric(self, tmp_data_dir):
        os.makedirs(os.path.join(tmp_data_dir, "not_a_user"))
        assert discover_users(tmp_data_dir, DB_FILENAME) == []


class TestUserConfig:
    def test_save_and_load(self, tmp_data_dir):
        os.makedirs(os.path.join(tmp_data_dir, "123"))
        config = {"key": "value"}
        save_user_config(tmp_data_dir, 123, config)
        assert get_user_config(tmp_data_dir, 123) == config

    def test_missing_config(self, tmp_data_dir):
        assert get_user_config(tmp_data_dir, 999) == {}


class TestSessions:
    def test_get_sessions(self, tmp_data_dir):
        conn = _create_user_db(tmp_data_dir, 100)
        conn.execute("INSERT INTO sessions (id, started_at, total_bets, profit, wagered) VALUES (1, '2025-01-01', 100, 0.5, 10.0)")
        conn.execute("INSERT INTO sessions (id, started_at, total_bets, profit, wagered) VALUES (2, '2025-01-02', 200, -0.3, 20.0)")
        conn.commit()
        conn.close()

        sessions = get_sessions(tmp_data_dir, DB_FILENAME, 100)
        assert len(sessions) == 2
        assert sessions[0]["id"] == 2  # DESC order

    def test_session_count(self, tmp_data_dir):
        conn = _create_user_db(tmp_data_dir, 100)
        conn.execute("INSERT INTO sessions (id, started_at) VALUES (1, '2025-01-01')")
        conn.commit()
        conn.close()
        assert get_session_count(tmp_data_dir, DB_FILENAME, 100) == 1

    def test_get_single_session(self, tmp_data_dir):
        conn = _create_user_db(tmp_data_dir, 100)
        conn.execute("INSERT INTO sessions (id, started_at, profit) VALUES (1, '2025-01-01', 0.5)")
        conn.commit()
        conn.close()
        s = get_session(tmp_data_dir, DB_FILENAME, 100, 1)
        assert s["profit"] == 0.5

    def test_missing_session(self, tmp_data_dir):
        _create_user_db(tmp_data_dir, 100).close()
        assert get_session(tmp_data_dir, DB_FILENAME, 100, 999) is None

    def test_no_db_returns_empty(self, tmp_data_dir):
        assert get_sessions(tmp_data_dir, DB_FILENAME, 999) == []
        assert get_session_count(tmp_data_dir, DB_FILENAME, 999) == 0


class TestSessionStats:
    def test_aggregate_stats(self, tmp_data_dir):
        conn = _create_user_db(tmp_data_dir, 100)
        conn.execute("INSERT INTO sessions (id, started_at, total_bets, profit, wagered, highest_balance) VALUES (1, '2025-01-01', 100, 0.5, 10.0, 5.0)")
        conn.execute("INSERT INTO sessions (id, started_at, total_bets, profit, wagered, highest_balance) VALUES (2, '2025-01-02', 200, -0.3, 20.0, 8.0)")
        conn.commit()
        conn.close()

        stats = get_session_stats(tmp_data_dir, DB_FILENAME, 100)
        assert stats["total_sessions"] == 2
        assert stats["total_bets"] == 300
        assert abs(stats["total_profit"] - 0.2) < 0.001
        assert stats["peak_balance"] == 8.0

    def test_no_db(self, tmp_data_dir):
        stats = get_session_stats(tmp_data_dir, DB_FILENAME, 999)
        assert stats["total_sessions"] == 0


class TestBets:
    def test_get_bets(self, tmp_data_dir):
        conn = _create_user_db(tmp_data_dir, 100)
        conn.execute("INSERT INTO sessions (id, started_at) VALUES (1, '2025-01-01')")
        for i in range(5):
            conn.execute(
                "INSERT INTO bets (session_id, timestamp, amount, state, profit, balance_after) VALUES (1, ?, 0.001, 'win', 0.001, ?)",
                (f"2025-01-01T00:{i:02d}:00", 1.0 + i * 0.001),
            )
        conn.commit()
        conn.close()

        bets = get_bets(tmp_data_dir, DB_FILENAME, 100, 1)
        assert len(bets) == 5

    def test_bet_count(self, tmp_data_dir):
        conn = _create_user_db(tmp_data_dir, 100)
        conn.execute("INSERT INTO sessions (id, started_at) VALUES (1, '2025-01-01')")
        conn.execute("INSERT INTO bets (session_id, timestamp, amount, state, profit, balance_after) VALUES (1, '2025-01-01', 0.001, 'win', 0.001, 1.0)")
        conn.commit()
        conn.close()
        assert get_bet_count(tmp_data_dir, DB_FILENAME, 100, 1) == 1


class TestBetsForChart:
    def test_chart_data(self, tmp_data_dir):
        conn = _create_user_db(tmp_data_dir, 100)
        conn.execute("INSERT INTO sessions (id, started_at) VALUES (1, '2025-01-01')")
        for i in range(10):
            conn.execute(
                "INSERT INTO bets (session_id, timestamp, amount, state, profit, balance_after) VALUES (1, ?, 0.001, 'win', 0.001, ?)",
                (f"2025-01-01T00:{i:02d}:00", 1.0 + i * 0.001),
            )
        conn.commit()
        conn.close()

        data = get_bets_for_chart(
            tmp_data_dir, DB_FILENAME, CHART_COLS,
            "amount", None, 100, 1,
        )
        assert len(data) == 10
        assert "time" in data[0]
        assert "value" in data[0]
        assert "ts" in data[0]

    def test_no_db(self, tmp_data_dir):
        data = get_bets_for_chart(
            tmp_data_dir, DB_FILENAME, CHART_COLS,
            "amount", None, 999, 1,
        )
        assert data == []


class TestPlatformStats:
    def test_aggregates_across_users(self, tmp_data_dir):
        for uid in [100, 200]:
            conn = _create_user_db(tmp_data_dir, uid)
            conn.execute("INSERT INTO sessions (id, started_at, total_bets, profit, wagered) VALUES (1, '2025-01-01', 50, 0.1, 5.0)")
            conn.commit()
            conn.close()

        stats = get_platform_stats(tmp_data_dir, DB_FILENAME)
        assert stats["total_users"] == 2
        assert stats["active_users"] == 2
        assert stats["total_bets"] == 100
