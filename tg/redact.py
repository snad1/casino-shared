"""Mask API keys and bearer tokens in log output for every bot."""

import logging
import re
import threading

MIN_SECRET_LENGTH = 8

_SECRETS: set[str] = set()
_LOCK = threading.Lock()

_API_KEY_RE = re.compile(r"(api_key=)[^&\s\"'>]+")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")


def mask_key(key: str) -> str:
    """First 8 and last 4 characters, or *** for anything short."""
    if len(key) < 16:
        return "***"
    return f"{key[:8]}...{key[-4:]}"


def register_secret(value: str) -> None:
    """Track a live API key so it is masked wherever it appears in a log line."""
    if value and len(value) >= MIN_SECRET_LENGTH:
        with _LOCK:
            _SECRETS.add(value)


def unregister_secret(value: str) -> None:
    with _LOCK:
        _SECRETS.discard(value)


def redact(text) -> str:
    """Mask registered keys plus api_key= values, bearer tokens and JWTs."""
    out = str(text)
    with _LOCK:
        secrets_now = list(_SECRETS)
    for key in secrets_now:
        if key in out:
            out = out.replace(key, mask_key(key))
    out = _API_KEY_RE.sub(lambda m: m.group(1) + "***", out)
    out = _BEARER_RE.sub(lambda m: m.group(1) + "***", out)
    out = _JWT_RE.sub("eyJ***", out)
    return out


class RedactingFormatter(logging.Formatter):
    """Formatter that runs every finished record through redact()."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))
