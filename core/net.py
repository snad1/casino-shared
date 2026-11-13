"""Proxy config helpers — pure functions, no I/O.

Ported from the WolfBet reference bot (core/net_helpers.py, CLI v2.28.0).
Each service wraps these with its own transport plumbing.

Not ported: classify_auto_stop_reason (WolfBet server-side auto-bet only);
parse_streak_delay lives in core/session_rules.py.
"""

from typing import Optional
from urllib.parse import urlparse

_PROXY_OFF_VALUES = ("", "off", "none", "direct", "0", "false", "disable", "no")


def validate_proxy(url) -> Optional[str]:
    """Return a normalized proxy URL or None.

    Accepts http://, https://, socks5://. Off-values (off/none/direct/0/...)
    return None. Raises ValueError on any other scheme.
    """
    if url is None:
        return None
    s = str(url).strip()
    if s.lower() in _PROXY_OFF_VALUES:
        return None
    if not (s.startswith("http://") or s.startswith("https://") or s.startswith("socks5://")):
        raise ValueError(
            f"Proxy must start with http://, https://, or socks5:// (got {s!r})"
        )
    return s


def mask_proxy(url) -> str:
    """Return scheme://host[:port] — strips userinfo, safe for display/logs.

    Returns "direct" for no proxy and "set" when the URL has no parseable host.
    """
    if not url:
        return "direct"
    try:
        p = urlparse(url)
        if p.hostname:
            if p.port:
                return f"{p.scheme}://{p.hostname}:{p.port}"
            return f"{p.scheme}://{p.hostname}"
    except Exception:
        pass
    return "set"
