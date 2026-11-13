"""Tests for shared/core/net.py.

Cases ported from wolfbet/tests/test_phase5k_core_helpers.py
(validate_proxy, mask_proxy).
"""

import pytest

from shared.core.net import mask_proxy, validate_proxy


# ── validate_proxy ────────────────────────────────────────
@pytest.mark.parametrize("url", [
    "http://host:8080", "https://host:8080", "socks5://host:1080",
])
def test_validate_accepted_schemes(url):
    assert validate_proxy(url) == url


def test_validate_strips_whitespace():
    assert validate_proxy("  http://host:8080  ") == "http://host:8080"


@pytest.mark.parametrize("value", [
    "off", "none", "direct", "0", "false", "disable", "no", "", None,
    "OFF", "  off  ",
])
def test_validate_off_values_return_none(value):
    assert validate_proxy(value) is None


@pytest.mark.parametrize("bad", ["ftp://x", "gopher://x", "x", "//host", "http:x"])
def test_validate_bad_scheme_raises(bad):
    with pytest.raises(ValueError, match="Proxy must start with"):
        validate_proxy(bad)


# ── mask_proxy ────────────────────────────────────────────
def test_mask_strips_userinfo():
    assert mask_proxy("http://user:pass@host:8080") == "http://host:8080"


def test_mask_no_port_no_none_leak():
    assert mask_proxy("http://host") == "http://host"
    assert "None" not in mask_proxy("http://host")


def test_mask_preserves_scheme_and_port():
    assert mask_proxy("https://proxy.example.com:443") == "https://proxy.example.com:443"
    assert mask_proxy("socks5://10.0.0.1:1080") == "socks5://10.0.0.1:1080"


def test_mask_none_and_empty_return_direct():
    assert mask_proxy(None) == "direct"
    assert mask_proxy("") == "direct"


def test_mask_unparseable_returns_set():
    assert mask_proxy("garbage") == "set"
    assert mask_proxy("http://host:notaport") == "set"
