"""Prefix rewrite + API shim for AstroAI Studio (dsh) CANFAR proxy."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("studio_canfar_proxy", ROOT / "studio-canfar-proxy.py")
assert SPEC and SPEC.loader
proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proxy)


def test_does_not_rewrite_quoted_api_channel() -> None:
    """Channel must stay \"/api\" (CHANNEL_PATTERN); shim rewrites network URLs."""
    proxy.PREFIX = "/session/contrib/abc"
    html = b'<html><head></head><body><script>fetch("/api/commands/execute")</script></body></html>'
    out = proxy.rewrite_body(html, "text/html")
    assert b'fetch("/api/commands/execute")' in out
    assert b'"/session/contrib/abc/api/commands/execute"' not in out
    assert b"data-astroai-api-shim" in out
    assert b"/session/contrib/abc" in out  # shim prefix JSON


def test_rewrite_prefixes_quoted_assets() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    html = b'<script>import("/assets/index.js")</script>'
    out = proxy.rewrite_body(html, "text/javascript")
    assert b'"/session/contrib/abc/assets/index.js"' in out


def test_rewrite_prefixes_quoted_astroai_agents() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    html = b"<script>const i = p.indexOf('/astroai-agents');</script>"
    out = proxy.rewrite_body(html, "text/html")
    assert b"/session/contrib/abc/astroai-agents" in out
    assert b"indexOf('/astroai-agents')" not in out


def test_rewrite_injects_agents_chip_and_shim() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    html = b"<html><head></head><body><h1>dsh</h1></body></html>"
    out = proxy.rewrite_body(html, "text/html")
    assert b'id="astroai-agents-chip"' in out
    assert b'href="/session/contrib/abc/astroai-agents/"' in out
    assert b'id="astroai-resource-banner"' in out
    assert b"Start batch compute" in out
    assert b"data-astroai-api-shim" in out
    assert b"window.WebSocket" in out


def test_rewrite_location_api() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    assert proxy.rewrite_location("/api/foo") == "/session/contrib/abc/api/foo"


def test_no_prefix_leaves_absolute_paths() -> None:
    proxy.PREFIX = ""
    html = b'<html><body><script>fetch("/api/x")</script></body></html>'
    out = proxy.rewrite_body(html, "text/html")
    assert b'fetch("/api/x")' in out
    assert b"data-astroai-api-shim" not in out


def test_is_websocket_request() -> None:
    class H:
        headers = {"Connection": "Upgrade", "Upgrade": "websocket"}

    assert proxy.is_websocket_request(H()) is True  # type: ignore[arg-type]

    class H2:
        headers = {"Connection": "keep-alive", "Upgrade": ""}

    assert proxy.is_websocket_request(H2()) is False  # type: ignore[arg-type]


if __name__ == "__main__":
    test_does_not_rewrite_quoted_api_channel()
    test_rewrite_prefixes_quoted_assets()
    test_rewrite_prefixes_quoted_astroai_agents()
    test_rewrite_injects_agents_chip_and_shim()
    test_rewrite_location_api()
    test_no_prefix_leaves_absolute_paths()
    test_is_websocket_request()
    print("ok")
