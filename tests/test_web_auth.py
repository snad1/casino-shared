"""Tests for shared/web/auth.py: JWT authentication."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from jose import jwt

from shared.web.auth import make_auth
from shared.web.websocket import make_authenticate_ws

SECRET = "test-secret-key-64-chars-long-for-testing-purposes-only-12345678"


@pytest.fixture
def auth():
    return make_auth(
        secret_key=SECRET,
        algorithm="HS256",
        expire_hours=24,
        cookie_name="test_token",
        is_session_valid_fn=lambda jti: jti != "revoked",
        revoke_session_fn=lambda jti: None,
    )


class TestCreateToken:
    def test_creates_valid_token(self, auth):
        token = auth["create_token"]("admin:1", "admin", "test-jti")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_custom_expiry(self, auth):
        token = auth["create_token"]("admin:1", "admin", "jti", timedelta(hours=1))
        payload = auth["decode_token"](token)
        assert payload is not None


class TestDecodeToken:
    def test_valid_token(self, auth):
        token = auth["create_token"]("admin:1", "admin", "test-jti")
        payload = auth["decode_token"](token)
        assert payload is not None
        assert payload["sub"] == "admin:1"
        assert payload["role"] == "admin"
        assert payload["jti"] == "test-jti"

    def test_invalid_token(self, auth):
        assert auth["decode_token"]("garbage.token.here") is None

    def test_revoked_session(self, auth):
        token = auth["create_token"]("admin:1", "admin", "revoked")
        assert auth["decode_token"](token) is None

    def test_skip_session_check(self, auth):
        token = auth["create_token"]("admin:1", "admin", "revoked")
        payload = auth["decode_token"](token, check_session=False)
        assert payload is not None


class TestGetTokenFromRequest:
    def _make_request(self, cookies=None, headers=None, query_params=None):
        req = MagicMock()
        req.cookies = cookies or {}
        req.headers = headers or {}
        req.query_params = query_params or {}
        return req

    def test_from_cookie(self, auth):
        req = self._make_request(cookies={"test_token": "my-token"})
        assert auth["get_token_from_request"](req) == "my-token"

    def test_from_bearer_header(self, auth):
        req = self._make_request(headers={"Authorization": "Bearer my-token"})
        assert auth["get_token_from_request"](req) == "my-token"

    def test_from_query_param(self, auth):
        req = self._make_request(query_params={"token": "my-token"})
        assert auth["get_token_from_request"](req) is None

    def test_cookie_takes_priority(self, auth):
        req = self._make_request(
            cookies={"test_token": "cookie-token"},
            headers={"Authorization": "Bearer header-token"},
        )
        assert auth["get_token_from_request"](req) == "cookie-token"

    def test_no_token(self, auth):
        req = self._make_request()
        assert auth["get_token_from_request"](req) is None


class TestGetCurrentAdmin:
    def test_valid_admin(self, auth):
        token = auth["create_token"]("admin:1", "admin", "jti")
        req = MagicMock()
        req.cookies = {"test_token": token}
        req.headers = {}
        req.query_params = {}
        result = auth["get_current_admin"](req)
        assert result["role"] == "admin"

    def test_non_admin_rejected(self, auth):
        from fastapi import HTTPException
        token = auth["create_token"]("tg:123", "tg_user", "jti")
        req = MagicMock()
        req.cookies = {"test_token": token}
        req.headers = {}
        req.query_params = {}
        with pytest.raises(HTTPException) as exc_info:
            auth["get_current_admin"](req)
        assert exc_info.value.status_code == 403


class TestGetCurrentTgUser:
    def test_valid_tg_user(self, auth):
        token = auth["create_token"]("tg:123", "tg_user", "jti")
        req = MagicMock()
        req.cookies = {"test_token": token}
        req.headers = {}
        req.query_params = {}
        result = auth["get_current_tg_user"](req)
        assert result["role"] == "tg_user"

    def test_admin_rejected(self, auth):
        from fastapi import HTTPException
        token = auth["create_token"]("admin:1", "admin", "jti")
        req = MagicMock()
        req.cookies = {"test_token": token}
        req.headers = {}
        req.query_params = {}
        with pytest.raises(HTTPException) as exc_info:
            auth["get_current_tg_user"](req)
        assert exc_info.value.status_code == 403


class TestGetOptionalUser:
    def test_with_token(self, auth):
        token = auth["create_token"]("admin:1", "admin", "jti")
        req = MagicMock()
        req.cookies = {"test_token": token}
        req.headers = {}
        req.query_params = {}
        result = auth["get_optional_user"](req)
        assert result is not None

    def test_without_token(self, auth):
        req = MagicMock()
        req.cookies = {}
        req.headers = {}
        req.query_params = {}
        assert auth["get_optional_user"](req) is None


class TestTokenWithoutJti:
    def test_rejected_when_session_check_is_on(self, auth):
        expire = datetime.now(timezone.utc) + timedelta(hours=1)
        token = jwt.encode({"sub": "tg:1", "role": "tg_user", "exp": expire},
                           SECRET, algorithm="HS256")
        assert auth["decode_token"](token) is None

    def test_accepted_for_the_one_time_link_exchange(self, auth):
        expire = datetime.now(timezone.utc) + timedelta(minutes=5)
        token = jwt.encode({"sub": "tg:1", "role": "tg_user", "exp": expire},
                           SECRET, algorithm="HS256")
        assert auth["decode_token"](token, check_session=False) is not None


class TestRequireUserAccess:
    def _request(self, token):
        req = MagicMock()
        req.cookies = {"test_token": token}
        req.headers = {}
        req.query_params = {}
        return req

    def test_owner_allowed(self, auth):
        token = auth["create_token"]("tg:111", "tg_user", "jti")
        assert auth["require_user_access"](111, self._request(token))["sub"] == "tg:111"

    def test_other_tg_user_forbidden(self, auth):
        from fastapi import HTTPException
        token = auth["create_token"]("tg:111", "tg_user", "jti")
        with pytest.raises(HTTPException) as exc:
            auth["require_user_access"](222, self._request(token))
        assert exc.value.status_code == 403

    def test_admin_allowed_on_any_user(self, auth):
        token = auth["create_token"]("admin:1", "admin", "jti")
        assert auth["require_user_access"](222, self._request(token))["role"] == "admin"


class _FakeWS:
    def __init__(self, cookies=None, headers=None):
        self.cookies = cookies or {}
        self.headers = headers or {}


class TestWebSocketAuthenticate:
    @pytest.fixture
    def authenticate(self, auth):
        return make_authenticate_ws(auth["decode_token"], "test_token")

    def _run(self, authenticate, ws):
        import asyncio
        return asyncio.run(authenticate(ws))

    def test_same_origin_cookie_accepted(self, auth, authenticate):
        token = auth["create_token"]("tg:1", "tg_user", "jti")
        ws = _FakeWS(cookies={"test_token": token},
                     headers={"origin": "https://bot.example", "host": "bot.example"})
        assert self._run(authenticate, ws)["sub"] == "tg:1"

    def test_foreign_origin_rejected(self, auth, authenticate):
        token = auth["create_token"]("tg:1", "tg_user", "jti")
        ws = _FakeWS(cookies={"test_token": token},
                     headers={"origin": "https://evil.example", "host": "bot.example"})
        assert self._run(authenticate, ws) is None

    def test_missing_origin_accepted(self, auth, authenticate):
        token = auth["create_token"]("tg:1", "tg_user", "jti")
        ws = _FakeWS(cookies={"test_token": token}, headers={"host": "bot.example"})
        assert self._run(authenticate, ws) is not None

    def test_no_cookie_rejected(self, authenticate):
        assert self._run(authenticate, _FakeWS(headers={"host": "bot.example"})) is None
