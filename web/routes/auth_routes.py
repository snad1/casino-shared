"""Authentication routes — login, logout, Telegram auth."""

from datetime import timedelta, datetime, timezone

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates


def make_auth_router(templates_dir, settings, create_token, decode_token,
                     cookie_name, verify_admin, create_web_session,
                     revoke_session, audit):
    """Create an auth router bound to a specific bot's config."""

    router = APIRouter(tags=["auth"])
    templates = Jinja2Templates(directory=str(templates_dir))

    @router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return templates.TemplateResponse("login.html", {"request": request, "error": None})

    @router.post("/login")
    async def login(request: Request, username: str = Form(...), password: str = Form(...)):
        admin = verify_admin(username, password)
        if not admin:
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "Invalid credentials",
            }, status_code=401)

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
        is_https = request.url.scheme == "https"
        response.set_cookie(
            cookie_name,
            token,
            httponly=True,
            secure=is_https,
            samesite="lax",
            max_age=settings.jwt_expire_hours * 3600,
        )
        return response

    @router.get("/logout")
    async def logout(request: Request):
        token = request.cookies.get(cookie_name)
        if token:
            payload = decode_token(token)
            if payload and payload.get("jti"):
                revoke_session(payload["jti"])
        response = RedirectResponse("/login", status_code=302)
        response.delete_cookie(cookie_name)
        return response

    @router.get("/auth/telegram")
    async def telegram_auth(request: Request, token: str = ""):
        """Exchange a one-time Telegram token for a session cookie."""
        if not token:
            return RedirectResponse("/login", status_code=302)

        payload = decode_token(token, check_session=False)
        if not payload or payload.get("role") != "tg_user":
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "Invalid or expired link. Request a new one from the bot.",
            }, status_code=401)

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
        is_https = request.url.scheme == "https"
        response.set_cookie(
            cookie_name,
            session_token,
            httponly=True,
            secure=is_https,
            samesite="lax",
            max_age=7 * 86400,
        )
        return response

    return router
