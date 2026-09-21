"""Prefix rewrite + API shim for AstroAI Studio (dsh) CANFAR proxy."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("studio_canfar_proxy", ROOT / "studio-canfar-proxy.py")
assert SPEC and SPEC.loader
proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proxy)


def test_keeps_bare_api_channel_string() -> None:
    """Channel must stay \"/api\" (CHANNEL_PATTERN)."""
    proxy.PREFIX = "/session/contrib/abc"
    js = b'const channel = "/api"; fetch(new URL(channel + "/x", origin));'
    out = proxy.rewrite_body(js, "text/javascript")
    assert b'const channel = "/api"' in out
    assert b'const channel = "/session/contrib/abc/api"' not in out


def test_rewrites_quoted_api_slash_paths() -> None:
    """"/api/…" (file, present, mux) must get the session prefix for img/href."""
    proxy.PREFIX = "/session/contrib/abc"
    js = (
        b'const FILE = "/api/file";'
        b'const MUX = "/api/remote.mux";'
        b'const OPEN = "/api/present.open";'
    )
    out = proxy.rewrite_body(js, "text/javascript")
    assert b'"/session/contrib/abc/api/file"' in out
    assert b'"/session/contrib/abc/api/remote.mux"' in out
    assert b'"/session/contrib/abc/api/present.open"' in out
    assert b'"/api/file"' not in out


def test_rewrites_quoted_assets() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    html = b'<script>import("/assets/index.js")</script>'
    out = proxy.rewrite_body(html, "text/javascript")
    assert b'"/session/contrib/abc/assets/index.js"' in out


def test_injects_api_shim_and_chips() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    html = b"<html><head></head><body><h1>dsh</h1></body></html>"
    out = proxy.rewrite_body(html, "text/html")
    assert b"data-astroai-api-shim" in out
    assert b"instanceof URL" in out  # fetch(URL) rewrite for directoryPicker RPCs
    assert b"ownsHost" in out  # Settings→Models needs isLoopback / ownsHost
    assert b"window.WebSocket" in out
    assert b"EventSource" in out
    assert b'id="astroai-terminal-chip"' in out
    assert b'href="/session/contrib/abc/astroai-terminal/"' in out
    assert b'id="astroai-agents-chip"' in out
    assert b'href="/session/contrib/abc/astroai-agents/"' in out
    assert b"astroai-resource-banner" not in out
    assert b'data-astroai-proxy-rev="8"' in out
    assert b"data-astroai-tab" in out  # branded tab stick


def test_rewrites_base_href_to_session_prefix() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    html = b'<html><head><base href="/"><link href="./assets/x.js"></head><body></body></html>'
    out = proxy.rewrite_body(html, "text/html")
    assert b'<base href="/session/contrib/abc/">' in out
    assert b'<base href="/">' not in out


def test_rewrite_location_api() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    assert proxy.rewrite_location("/api/foo") == "/session/contrib/abc/api/foo"
    assert proxy.rewrite_location("/") == "/session/contrib/abc/"
    assert proxy.rewrite_location("/?x=1") == "/session/contrib/abc/?x=1"
    assert proxy.rewrite_location("/session/contrib/abc/") == "/session/contrib/abc/"


def test_upstream_path_strips_session_prefix() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    assert proxy.upstream_path("/session/contrib/abc/") == "/"
    assert proxy.upstream_path("/session/contrib/abc/?token=t") == "/?token=t"
    assert proxy.upstream_path("/session/contrib/abc/assets/x.js") == "/assets/x.js"
    assert proxy.upstream_path("/api/x") == "/api/x"
    proxy.PREFIX = ""
    assert proxy.upstream_path("/session/contrib/abc/") == "/session/contrib/abc/"


def test_no_prefix_leaves_absolute_paths() -> None:
    proxy.PREFIX = ""
    html = b'<html><body><script>fetch("/api/x")</script></body></html>'
    out = proxy.rewrite_body(html, "text/html")
    assert b'fetch("/api/x")' in out
    assert b"data-astroai-api-shim" not in out


def test_is_websocket_request() -> None:
    class H:
        headers: dict[str, str]

    h = H()
    h.headers = {"Connection": "Upgrade", "Upgrade": "websocket"}
    assert proxy.is_websocket_request(h) is True
    h.headers = {"Connection": "keep-alive"}
    assert proxy.is_websocket_request(h) is False


def test_index_token_redirect_adds_token(tmp_path: Path, monkeypatch) -> None:
    token_file = tmp_path / "dsh-web-token"
    token_file.write_text("sekrit\n", encoding="utf-8")
    monkeypatch.setenv("ASTROAI_DSH_TOKEN_FILE", str(token_file))
    proxy.TOKEN_FILE = str(token_file)
    proxy.PREFIX = "/session/contrib/abc"
    loc = proxy.index_token_redirect("/", None)
    assert loc == "/session/contrib/abc/?token=sekrit"
    assert proxy.index_token_redirect("/?token=sekrit", None) is None
    assert proxy.index_token_redirect("/", "dsh-auth-xyz=1") is None
    assert proxy.index_token_redirect("/api/x", None) is None


def test_index_token_redirect_without_prefix(tmp_path: Path, monkeypatch) -> None:
    token_file = tmp_path / "tok"
    token_file.write_text("t1", encoding="utf-8")
    monkeypatch.setenv("ASTROAI_DSH_TOKEN_FILE", str(token_file))
    proxy.TOKEN_FILE = str(token_file)
    proxy.PREFIX = ""
    assert proxy.index_token_redirect("/", None) == "/?token=t1"


def test_is_index_path() -> None:
    proxy.PREFIX = ""
    assert proxy._is_index_path("/") is True
    assert proxy._is_index_path("/?token=x") is True
    assert proxy._is_index_path("/api/x") is False
    proxy.PREFIX = "/session/contrib/abc"
    assert proxy._is_index_path("/session/contrib/abc/") is True
    assert proxy._is_index_path("/session/contrib/abc") is True
    assert proxy._is_index_path("/session/contrib/abc/api") is False


def test_starting_html_is_refreshable() -> None:
    assert b'meta http-equiv="refresh"' in proxy.STARTING_HTML
    assert b"AstroAI Studio is starting" in proxy.STARTING_HTML


if __name__ == "__main__":
    import tempfile

    test_keeps_bare_api_channel_string()
    test_rewrites_quoted_api_slash_paths()
    test_rewrites_quoted_assets()
    test_injects_api_shim_and_chips()
    test_rewrites_base_href_to_session_prefix()
    test_rewrite_location_api()
    test_upstream_path_strips_session_prefix()
    test_no_prefix_leaves_absolute_paths()
    test_is_websocket_request()
    test_is_index_path()
    test_starting_html_is_refreshable()
    with tempfile.TemporaryDirectory() as td:
        tok = Path(td) / "dsh-web-token"
        tok.write_text("sekrit\n", encoding="utf-8")
        proxy.TOKEN_FILE = str(tok)
        proxy.PREFIX = "/session/contrib/abc"
        assert proxy.index_token_redirect("/", None) == "/session/contrib/abc/?token=sekrit"
        proxy.PREFIX = ""
        assert proxy.index_token_redirect("/", None) == "/?token=sekrit"
    print("ok")
