"""Startup rules for the web admin's credentials, shared by every bot."""

DEFAULT_SECRET_KEY = "change-me-to-a-random-64-char-string"
DEFAULT_ADMIN_PASS = "changeme123"
MIN_SECRET_BYTES = 32
MIN_ADMIN_PASS_CHARS = 12


def validate_web_credentials(secret_key: str, admin_pass: str, env_prefix: str = "") -> None:
    """Refuse a web admin that would start with the shipped or a weak credential.

    env_prefix names the bot's setting (for example "GOATED_WEB_") so the
    startup error points at the variable to fix.
    """
    secret_name = f"{env_prefix}SECRET_KEY" if env_prefix else "the web secret key"
    pass_name = f"{env_prefix}ADMIN_PASS" if env_prefix else "the web admin password"

    if secret_key == DEFAULT_SECRET_KEY or len(secret_key.encode()) < MIN_SECRET_BYTES:
        raise ValueError(
            f"{secret_name} must be at least {MIN_SECRET_BYTES} bytes and must not be "
            "the shipped default. "
            'Generate one with: python3 -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    if admin_pass == DEFAULT_ADMIN_PASS or len(admin_pass) < MIN_ADMIN_PASS_CHARS:
        raise ValueError(
            f"{pass_name} must be at least {MIN_ADMIN_PASS_CHARS} characters and must not "
            "be the shipped default"
        )
