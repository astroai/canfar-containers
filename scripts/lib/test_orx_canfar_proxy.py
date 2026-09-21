"""Prefix rewrite + Terminal/AstroAI chips for OpenResearch CANFAR proxy."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("orx_canfar_proxy", ROOT / "orx-canfar-proxy.py")
assert SPEC and SPEC.loader
proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proxy)


def test_rewrite_prefixes_quoted_astroai_agents() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    html = b"<script>const i = p.indexOf('/astroai-agents');</script>"
    out = proxy.rewrite_body(html, "text/html")
    assert b"/session/contrib/abc/astroai-agents" in out
    assert b"indexOf('/astroai-agents')" not in out


def test_split_marker_survives_rewrite() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    html = b"<html><body><script>const marker = '/astroai-' + 'agents';</script></body></html>"
    out = proxy.rewrite_body(html, "text/html")
    assert b"/astroai-' + 'agents" in out
    assert b"indexOf('/session/contrib/abc/astroai-agents')" not in out


def test_injects_terminal_and_agents_chips() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    html = b"<html><body><h1>orx</h1></body></html>"
    out = proxy.rewrite_body(html, "text/html")
    assert b'id="astroai-terminal-chip"' in out
    assert b'href="/session/contrib/abc/astroai-terminal/"' in out
    assert b'id="astroai-agents-chip"' in out
    assert b'href="/session/contrib/abc/astroai-agents/"' in out


def test_rewrite_prefixes_astroai_terminal() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    html = b'<a href="/astroai-terminal/">t</a>'
    out = proxy.rewrite_body(html, "text/html")
    assert b'href="/session/contrib/abc/astroai-terminal/"' in out


def test_injects_tanstack_basepath() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    js = b'vBn=cW({routeTree:gBn,context:{queryClient:rt},trailingSlash:"never",defaultPendingComponent:iS})'
    out = proxy.rewrite_body(js, "text/javascript")
    assert b'basepath:"/session/contrib/abc",trailingSlash:"always"' in out
    assert b'trailingSlash:"never"' not in out


def test_html_cache_busts_assets() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    html = (
        b'<html><head>'
        b'<script type="module" crossorigin src="/assets/index-abc.js"></script>'
        b'<link rel="stylesheet" href="/assets/index-abc.css">'
        b'</head><body></body></html>'
    )
    out = proxy.rewrite_body(html, "text/html")
    assert b'src="/session/contrib/abc/assets/index-abc.js?astroai_bp=1"' in out
    assert b'href="/session/contrib/abc/assets/index-abc.css?astroai_bp=1"' in out


if __name__ == "__main__":
    test_rewrite_prefixes_quoted_astroai_agents()
    test_split_marker_survives_rewrite()
    test_injects_terminal_and_agents_chips()
    test_rewrite_prefixes_astroai_terminal()
    test_injects_tanstack_basepath()
    test_html_cache_busts_assets()
    print("ok")
