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


def test_rewrite_prefixes_orx_control_paths() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    js = b'Xvn=e=>jt("/_orx/runtime",e);if(r.startsWith("/_orx/")){}'
    out = proxy.rewrite_body(js, "text/javascript")
    assert b'jt("/session/contrib/abc/_orx/runtime"' in out
    assert b'startsWith("/session/contrib/abc/_orx/")' in out
    assert proxy.rewrite_location("/_orx/runtime") == "/session/contrib/abc/_orx/runtime"


def test_rewrite_prefixes_location_host_websocket() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    js = b"new WebSocket(`${ut}//${location.host}/api/harnesses/setup?harness=opencode`)"
    out = proxy.rewrite_body(js, "text/javascript")
    assert b"${location.host}/session/contrib/abc/api/harnesses/setup?" in out
    assert proxy.rewrite_body(out, "text/javascript") == out


def test_same_origin_requests_present_loopback_origin() -> None:
    headers = {"Origin": "https://ws-uv.canfar.net", "Sec-Fetch-Site": "same-origin"}
    assert proxy.orx_identity_headers(headers) == {
        "Host": f"{proxy.ORX_HOST}:{proxy.ORX_PORT}",
        "Origin": f"http://{proxy.ORX_HOST}:{proxy.ORX_PORT}",
    }


def test_websocket_without_fetch_metadata_matches_origin_to_host() -> None:
    # Chromium sends no Sec-Fetch-Site on WebSocket handshakes.
    for headers in (
        {"Origin": "https://ws-uv.canfar.net", "Host": "ws-uv.canfar.net"},
        {
            "Origin": "https://ws-uv.canfar.net",
            "Host": "pod:5000",
            "X-Forwarded-Host": "ws-uv.canfar.net",
        },
    ):
        assert proxy.orx_identity_headers(headers)["Origin"] == (
            f"http://{proxy.ORX_HOST}:{proxy.ORX_PORT}"
        )


def test_cross_site_requests_keep_browser_origin() -> None:
    for site in ("cross-site", "same-site", "none", None):
        headers = {"Origin": "https://evil.example", "Host": "ws-uv.canfar.net"}
        if site:
            headers["Sec-Fetch-Site"] = site
        assert proxy.orx_identity_headers(headers) == {
            "Host": f"{proxy.ORX_HOST}:{proxy.ORX_PORT}",
        }
    spoofed = {
        "Origin": "https://ws-uv.canfar.net",
        "Host": "ws-uv.canfar.net",
        "Sec-Fetch-Site": "cross-site",
    }
    assert "Origin" not in proxy.orx_identity_headers(spoofed)


def test_injects_tanstack_basepath() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    js = b'vBn=cW({routeTree:gBn,context:{queryClient:rt},trailingSlash:"never",defaultPendingComponent:iS})'
    out = proxy.rewrite_body(js, "text/javascript")
    # "always" sends nested routes (/projects/<id>/tasks/<id>/) to the router's Not Found.
    assert b'basepath:"/session/contrib/abc",trailingSlash:"preserve"' in out
    assert b'trailingSlash:"never"' not in out
    assert b'trailingSlash:"always"' not in out
    assert proxy.rewrite_body(out, "text/javascript") == out


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
    test_rewrite_prefixes_orx_control_paths()
    test_rewrite_prefixes_location_host_websocket()
    test_same_origin_requests_present_loopback_origin()
    test_websocket_without_fetch_metadata_matches_origin_to_host()
    test_cross_site_requests_keep_browser_origin()
    test_injects_tanstack_basepath()
    test_html_cache_busts_assets()
    print("ok")
