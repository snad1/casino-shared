"""JWT authentication: admin and Telegram user support."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Request, HTTPException, status
from jose import JWTError, jwt


def make_auth(secret_key: str, algorithm: str, expire_hours: int,
              cookie_name: str, is_session_valid_fn, revoke_session_fn):
    """Create all auth functions bound to a specific bot's config.

    Returns a dict of auth functions and the cookie name.
    """

    def create_token(subject: str, role: str, jti: str, expires_delta: timedelta | None = None) -> str:
        expire = datetime.now(timezone.utc) + (expires_delta or timedelta(hours=expire_hours))
        payload = {
            "sub": subject,
            "role": role,
            "jti": jti,
            "exp": expire,
        }
        return jwt.encode(payload, secret_key, algorithm=algorithm)

    def decode_token(token: str, check_session: bool = True) -> dict | None:
        try:
            payload = jwt.decode(token, secret_key, algorithms=[algorithm])
            if check_session:
                jti = payload.get("jti")
                if not jti or not is_session_valid_fn(jti):
                    return None
            return payload
        except JWTError:
            return None

    def get_token_from_request(request: Request) -> str | None:
        token = request.cookies.get(cookie_name)
        if token:
            return token
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return None

    def _get_payload(request: Request) -> dict:
        token = get_token_from_request(request)
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
        payload = decode_token(token)
        if not payload:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
        return payload

    def get_current_admin(request: Request) -> dict:
        payload = _get_payload(request)
        if payload.get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
        return payload

    def get_current_tg_user(request: Request) -> dict:
        payload = _get_payload(request)
        if payload.get("role") != "tg_user":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Telegram user access required")
        return payload

    def get_current_user(request: Request) -> dict:
        return _get_payload(request)

    def get_optional_user(request: Request) -> Optional[dict]:
        token = get_token_from_request(request)
        if not token:
            return None
        return decode_token(token)

    def require_user_access(user_id: int, request: Request) -> dict:
        """Allow admins, and Telegram users only on their own user id."""
        payload = _get_payload(request)
        if payload.get("role") == "admin":
            return payload
        if payload.get("sub") != f"tg:{user_id}":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only view your own data",
            )
        return payload

    return {
        "create_token": create_token,
        "decode_token": decode_token,
        "get_token_from_request": get_token_from_request,
        "get_current_admin": get_current_admin,
        "get_current_tg_user": get_current_tg_user,
        "get_current_user": get_current_user,
        "get_optional_user": get_optional_user,
        "require_user_access": require_user_access,
        "COOKIE_NAME": cookie_name,
        "revoke_session": revoke_session_fn,
    }
