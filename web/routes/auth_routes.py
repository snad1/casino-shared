"""Authentication routes: login, logout, Telegram auth."""

import hmac
import secrets
from datetime import timedelta, datetime, timezone

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

CSRF_COOKIE = "csrf_token"


def _client_key(request: Request, username: str) -> str:
    """Throttle key: first forwarded hop (nginx is the only listener) plus the username."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    else:
        ip = request.client.host if request.client else ""
    return f"{ip}|{username}"


def _cookie_secure(request: Request, settings) -> bool:
    return settings.app_env == "production" or request.url.scheme == "https"


def make_auth_router(templates_dir, settings, create_token, decode_token,
                     cookie_name, verify_admin, create_web_session,
                     revoke_session, audit, consume_login_token=None,
                     login_locked_until=None, record_login_failure=None,
                     clear_login_failures=None):
    """Create an auth router bound to a specific bot's config.

    The lockout and one-time-link callbacks are optional so a bot that has not
    been ported yet keeps its current behaviour.
    """

    router = APIRouter(tags=["auth"])
    templates = Jinja2Templates(directory=str(templates_dir))

    def _set_session_cookies(response, token: str, csrf: str, max_age: int,
                             secure: bool):
        response.set_cookie(cookie_name, token, httponly=True, secure=secure,
                            samesite="lax", max_age=max_age)
        response.set_cookie(CSRF_COOKIE, csrf, httponly=True, secure=secure,
                            samesite="lax", max_age=max_age)

    def _login_error(request: Request, message: str, status_code: int):
        return templates.TemplateResponse(request, "login.html", {
            "error": message,
        }, status_code=status_code)

    @router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @router.post("/login")
    def login(request: Request, username: str = Form(...), password: str = Form(...)):
        key = _client_key(request, username)
        if login_locked_until and login_locked_until(key):
            return _login_error(
                request,
                "Too many failed attempts. Try again in a few minutes.",
                429,
            )

        admin = verify_admin(username, password)
        if not admin:
            if record_login_failure:
                record_login_failure(key)
            return _login_error(request, "Invalid credentials", 401)

        if clear_login_failures:
            clear_login_failures(key)

        expires = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
        jti = create_web_session("admin", str(admin["id"]), expires.isoformat())
        token = create_token(
            subject=f"admin:{admin['id']}",
            role="admin",
            jti=jti,
            expires_delta=timedelta(hours=settings.jwt_expire_hours),
        )

        audit(f"admin:{username}", "login", "Admin login")
        response = RedirectResponse("/admin/", status_code=302)
        _set_session_cookies(
            response, token, secrets.token_urlsafe(32),
            settings.jwt_expire_hours * 3600, _cookie_secure(request, settings),
        )
        return response

    @router.post("/logout")
    def logout(request: Request, csrf_token: str = Form("")):
        expected = request.cookies.get(CSRF_COOKIE, "")
        if not expected or not hmac.compare_digest(csrf_token, expected):
            raise HTTPException(status_code=403, detail="Invalid request")

        token = request.cookies.get(cookie_name)
        if token:
            payload = decode_token(token)
            if payload and payload.get("jti"):
                revoke_session(payload["jti"])
        response = RedirectResponse("/login", status_code=302)
        response.delete_cookie(cookie_name)
        response.delete_cookie(CSRF_COOKIE)
        return response

    @router.get("/auth/telegram")
    def telegram_auth(request: Request, token: str = ""):
        """Exchange a one-time Telegram token for a session cookie."""
        if not token:
            return RedirectResponse("/login", status_code=302)

        payload = decode_token(token, check_session=False)
        link_jti = payload.get("jti") if payload else None
        if not payload or payload.get("role") != "tg_user" or not link_jti:
            return _login_error(
                request,
                "Invalid or expired link. Request a new one from the bot.",
                401,
            )
        if consume_login_token and not consume_login_token(link_jti):
            return _login_error(
                request,
                "Invalid or expired link. Request a new one from the bot.",
                401,
            )

        user_id = payload["sub"].split(":")[1]
        expires = datetime.now(timezone.utc) + timedelta(days=7)
        jti = create_web_session("telegram", user_id, expires.isoformat())
        session_token = create_token(
            subject=payload["sub"],
            role="tg_user",
            jti=jti,
            expires_delta=timedelta(days=7),
        )

        audit(f"tg:{user_id}", "login", "Telegram user login via deep link")
        response = RedirectResponse("/dashboard/", status_code=302)
        _set_session_cookies(
            response, session_token, secrets.token_urlsafe(32),
            7 * 86400, _cookie_secure(request, settings),
        )
        return response

    return router
