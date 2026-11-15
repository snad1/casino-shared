"""Tests for shared/web/settings.py."""

import pytest

from shared.web.settings import (
    DEFAULT_ADMIN_PASS, DEFAULT_SECRET_KEY, validate_web_credentials,
)

STRONG_SECRET = "s" * 40
STRONG_PASS = "a-strong-password"
PREFIX = "GOATED_WEB_"


class TestValidateWebCredentials:
    def test_strong_pair_passes(self):
        validate_web_credentials(STRONG_SECRET, STRONG_PASS, PREFIX)

    def test_shipped_secret_rejected(self):
        with pytest.raises(ValueError, match="SECRET_KEY"):
            validate_web_credentials(DEFAULT_SECRET_KEY, STRONG_PASS, PREFIX)

    def test_short_secret_rejected(self):
        with pytest.raises(ValueError, match="SECRET_KEY"):
            validate_web_credentials("x" * 31, STRONG_PASS, PREFIX)

    def test_shipped_password_rejected(self):
        with pytest.raises(ValueError, match="ADMIN_PASS"):
            validate_web_credentials(STRONG_SECRET, DEFAULT_ADMIN_PASS, PREFIX)

    def test_short_password_rejected(self):
        with pytest.raises(ValueError, match="ADMIN_PASS"):
            validate_web_credentials(STRONG_SECRET, "short-pass1", PREFIX)

    def test_without_a_prefix_the_message_stays_readable(self):
        with pytest.raises(ValueError, match="admin password"):
            validate_web_credentials(STRONG_SECRET, DEFAULT_ADMIN_PASS)

    def test_never_echoes_the_value(self):
        with pytest.raises(ValueError) as exc:
            validate_web_credentials(STRONG_SECRET, DEFAULT_ADMIN_PASS, PREFIX)
        assert DEFAULT_ADMIN_PASS not in str(exc.value)
