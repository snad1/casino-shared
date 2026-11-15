"""Tests for shared/tg/redact.py."""

import logging

from shared.tg.redact import (
    RedactingFormatter, mask_key, redact, register_secret, unregister_secret,
)


class TestMaskKey:
    def test_long_key_keeps_the_edges(self):
        masked = mask_key("abcdefghijklmnopqrstuvwxyz")
        assert masked.startswith("abcdefgh") and masked.endswith("wxyz")
        assert "ijklmno" not in masked

    def test_short_key_is_fully_masked(self):
        assert mask_key("short") == "***"


class TestRedact:
    def test_registered_key_masked_anywhere(self):
        register_secret("supersecretkey123456")
        try:
            assert "supersecretkey123456" not in redact("url=https://x/bets/supersecretkey123456/play")
        finally:
            unregister_secret("supersecretkey123456")

    def test_api_key_query_masked(self):
        assert redact("GET /dice/play?api_key=abcdef123456&x=1") == "GET /dice/play?api_key=***&x=1"

    def test_bearer_masked(self):
        assert redact("Authorization: Bearer abcdef123456") == "Authorization: Bearer ***"

    def test_jwt_masked(self):
        assert "eyJ***" in redact("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghij")

    def test_unregistered_text_untouched(self):
        assert redact("balance 1.5 btc") == "balance 1.5 btc"

    def test_short_key_is_not_registered(self):
        register_secret("short")
        try:
            assert redact("short") == "short"
        finally:
            unregister_secret("short")


class TestRedactingFormatter:
    def test_formatter_masks_the_record(self):
        register_secret("supersecretkey123456")
        try:
            record = logging.LogRecord("x", logging.WARNING, __file__, 1,
                                       "failed for supersecretkey123456", None, None)
            formatted = RedactingFormatter("%(message)s").format(record)
            assert "supersecretkey123456" not in formatted
        finally:
            unregister_secret("supersecretkey123456")
