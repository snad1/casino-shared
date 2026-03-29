"""Tests for shared/web/database.py — admin users, sessions, audit."""

import sqlite3

import pytest

from shared.web.database import (
    _hash_password, _verify_password,
    get_db, init_db, verify_admin, change_admin_password,
    sync_tg_user, get_tg_users, get_tg_user, update_tg_user,
    audit, get_audit_log,
    create_web_session, is_session_valid, revoke_session,
)


@pytest.fixture
def db(tmp_db):
    init_db(tmp_db, "admin", "testpass")
    return tmp_db


class TestPasswordHashing:
    def test_hash_and_verify(self):
        h = _hash_password("mypassword")
        assert _verify_password("mypassword", h) is True
        assert _verify_password("wrong", h) is False

    def test_different_salts(self):
        h1 = _hash_password("same")
        h2 = _hash_password("same")
        assert h1 != h2

    def test_verify_invalid_format(self):
        assert _verify_password("pass", "garbage") is False
        assert _verify_password("pass", "") is False


class TestInitDb:
    def test_creates_tables(self, db):
        conn = get_db(db)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()
        assert "admin_users" in tables
        assert "tg_users" in tables
        assert "web_sessions" in tables
        assert "audit_log" in tables

    def test_creates_admin_user(self, db):
        admin = verify_admin(db, "admin", "testpass")
        assert admin is not None
        assert admin["username"] == "admin"
        assert admin["role"] == "admin"

    def test_idempotent(self, db):
        init_db(db, "admin", "testpass")
        conn = get_db(db)
        count = conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
        conn.close()
        assert count == 1


class TestVerifyAdmin:
    def test_valid_credentials(self, db):
        result = verify_admin(db, "admin", "testpass")
        assert result is not None
        assert result["username"] == "admin"

    def test_wrong_password(self, db):
        assert verify_admin(db, "admin", "wrong") is None

    def test_nonexistent_user(self, db):
        assert verify_admin(db, "nobody", "pass") is None

    def test_updates_last_login(self, db):
        verify_admin(db, "admin", "testpass")
        conn = get_db(db)
        row = conn.execute("SELECT last_login FROM admin_users WHERE username='admin'").fetchone()
        conn.close()
        assert row["last_login"] is not None


class TestChangeAdminPassword:
    def test_change_password(self, db):
        admin = verify_admin(db, "admin", "testpass")
        result = change_admin_password(db, admin["id"], "testpass", "newpass")
        assert result is True
        assert verify_admin(db, "admin", "newpass") is not None
        assert verify_admin(db, "admin", "testpass") is None

    def test_wrong_old_password(self, db):
        admin = verify_admin(db, "admin", "testpass")
        result = change_admin_password(db, admin["id"], "wrong", "newpass")
        assert result is False


class TestTgUsers:
    def test_sync_new_user(self, db):
        sync_tg_user(db, 12345, "testuser", "free")
        user = get_tg_user(db, 12345)
        assert user is not None
        assert user["username"] == "testuser"
        assert user["tier"] == "free"

    def test_sync_updates_existing(self, db):
        sync_tg_user(db, 12345, "old")
        sync_tg_user(db, 12345, "new", "vip")
        user = get_tg_user(db, 12345)
        assert user["username"] == "new"
        assert user["tier"] == "vip"

    def test_get_tg_users(self, db):
        sync_tg_user(db, 111, "a")
        sync_tg_user(db, 222, "b")
        users = get_tg_users(db)
        assert len(users) == 2

    def test_get_nonexistent_user(self, db):
        assert get_tg_user(db, 99999) is None

    def test_update_tg_user(self, db):
        sync_tg_user(db, 12345, "user")
        update_tg_user(db, 12345, tier="premium", notes="test note")
        user = get_tg_user(db, 12345)
        assert user["tier"] == "premium"
        assert user["notes"] == "test note"

    def test_update_rejects_bad_columns(self, db):
        sync_tg_user(db, 12345, "user")
        update_tg_user(db, 12345, password_hash="evil")
        user = get_tg_user(db, 12345)
        assert "password_hash" not in user or user.get("password_hash") is None

    def test_update_blocks_user(self, db):
        sync_tg_user(db, 12345, "user")
        update_tg_user(db, 12345, is_blocked=1)
        user = get_tg_user(db, 12345)
        assert user["is_blocked"] == 1


class TestAudit:
    def test_create_and_retrieve(self, db):
        audit(db, "admin:test", "login", "Test login")
        logs = get_audit_log(db)
        assert len(logs) == 1
        assert logs[0]["actor"] == "admin:test"
        assert logs[0]["action"] == "login"

    def test_limit(self, db):
        for i in range(10):
            audit(db, "actor", f"action_{i}")
        logs = get_audit_log(db, limit=5)
        assert len(logs) == 5

    def test_returns_multiple(self, db):
        audit(db, "a", "first")
        audit(db, "a", "second")
        logs = get_audit_log(db)
        assert len(logs) == 2
        actions = {l["action"] for l in logs}
        assert actions == {"first", "second"}


class TestWebSessions:
    def test_create_and_validate(self, db):
        jti = create_web_session(db, "admin", "1", "2099-01-01T00:00:00")
        assert jti is not None
        assert is_session_valid(db, jti) is True

    def test_revoke_session(self, db):
        jti = create_web_session(db, "admin", "1", "2099-01-01T00:00:00")
        revoke_session(db, jti)
        assert is_session_valid(db, jti) is False

    def test_nonexistent_session(self, db):
        assert is_session_valid(db, "fake-jti") is False
