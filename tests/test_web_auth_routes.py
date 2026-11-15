"""Tests for shared/web/routes/auth_routes.py: login, logout, Telegram exchange."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from shared.web.auth import make_auth
from shared.web.database import (
    audit, clear_login_failures, consume_login_token, create_web_session,
    init_db, is_session_valid, login_locked_until, record_login_failure,
    revoke_session, verify_admin,
)
from shared.web.routes.auth_routes import make_auth_router

SECRET = "test-secret-key-64-chars-long-for-testing-purposes-only-12345678"
COOKIE = "test_token"
ADMIN_PASS = "admin-pass-123456"


@pytest.fixture
def templates_dir(tmp_path):
    d = tmp_path / "templates"
    d.mkdir()
    (d / "login.html").write_text("<p>{{ error or '' }}</p>")
    return d


def _build(tmp_db, templates_dir, app_env="testing"):
    """Wire the auth router over a temporary web database."""
    init_db(tmp_db, "admin", ADMIN_PASS)
    settings = SimpleNamespace(jwt_expire_hours=24, app_env=app_env)
    auth = make_auth(
        secret_key=SECRET,
        algorithm="HS256",
        expire_hours=24,
        cookie_name=COOKIE,
        is_session_valid_fn=lambda jti: is_session_valid(tmp_db, jti),
        revoke_session_fn=lambda jti: revoke_session(tmp_db, jti),
    )
    router = make_auth_router(
        templates_dir=templates_dir,
        settings=settings,
        create_token=auth["create_token"],
        decode_token=auth["decode_token"],
        cookie_name=COOKIE,
        verify_admin=lambda user, pw: verify_admin(tmp_db, user, pw),
        create_web_session=lambda kind, uid, exp: create_web_session(tmp_db, kind, uid, exp),
        revoke_session=lambda jti: revoke_session(tmp_db, jti),
        audit=lambda actor, action, detail="": audit(tmp_db, actor, action, detail),
        consume_login_token=lambda jti: consume_login_token(tmp_db, jti),
        login_locked_until=lambda key: login_locked_until(tmp_db, key),
        record_login_failure=lambda key: record_login_failure(tmp_db, key),
        clear_login_failures=lambda key: clear_login_failures(tmp_db, key),
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), auth


def _login(client, password=ADMIN_PASS):
    return client.post("/login", data={"username": "admin", "password": password},
                       follow_redirects=False)


def test_logout_get_not_allowed(tmp_db, templates_dir):
    client, _ = _build(tmp_db, templates_dir)
    assert client.get("/login").status_code == 200
    assert client.post("/logout").status_code == 403
    assert client.get("/logout").status_code == 405


def test_logout_requires_matching_csrf(tmp_db, templates_dir):
    client, _ = _build(tmp_db, templates_dir)
    response = _login(client)
    csrf = client.cookies.get("csrf_token")
    assert response.status_code == 302
    assert csrf
    assert client.post("/logout", data={"csrf_token": "wrong"}).status_code == 403
    logout = client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)
    assert logout.status_code == 302
    assert COOKIE not in client.cookies


def test_logout_revokes_the_session(tmp_db, templates_dir):
    client, auth = _build(tmp_db, templates_dir)
    _login(client)
    token = client.cookies.get(COOKIE)
    jti = auth["decode_token"](token, check_session=False)["jti"]
    assert is_session_valid(tmp_db, jti)
    client.post("/logout", data={"csrf_token": client.cookies.get("csrf_token")})
    assert not is_session_valid(tmp_db, jti)


def test_login_lockout_after_five_failures(tmp_db, templates_dir):
    client, _ = _build(tmp_db, templates_dir)
    for _ in range(5):
        assert _login(client, "wrong-password").status_code == 401
    assert _login(client).status_code == 429


def test_successful_login_clears_the_failure_counter(tmp_db, templates_dir):
    client, _ = _build(tmp_db, templates_dir)
    for _ in range(4):
        _login(client, "wrong-password")
    assert _login(client).status_code == 302
    for _ in range(4):
        assert _login(client, "wrong-password").status_code == 401


def test_telegram_link_is_single_use(tmp_db, templates_dir):
    client, auth = _build(tmp_db, templates_dir)
    link = auth["create_token"]("tg:7", "tg_user", "link-jti", timedelta(minutes=5))
    first = client.get(f"/auth/telegram?token={link}", follow_redirects=False)
    assert first.status_code == 302
    second = client.get(f"/auth/telegram?token={link}", follow_redirects=False)
    assert second.status_code == 401
    assert "Invalid or expired link" in second.text


def test_telegram_link_without_jti_rejected(tmp_db, templates_dir):
    client, _ = _build(tmp_db, templates_dir)
    expire = datetime.now(timezone.utc) + timedelta(minutes=5)
    link = jwt.encode({"sub": "tg:7", "role": "tg_user", "exp": expire}, SECRET, algorithm="HS256")
    response = client.get(f"/auth/telegram?token={link}", follow_redirects=False)
    assert response.status_code == 401


def test_secure_cookies_forced_in_production(tmp_db, templates_dir):
    client, _ = _build(tmp_db, templates_dir, app_env="production")
    response = _login(client)
    cookies = response.headers.get_list("set-cookie")
    assert any(c.startswith(COOKIE) and "secure" in c.lower() for c in cookies)
    assert any(c.startswith("csrf_token") and "secure" in c.lower() for c in cookies)
