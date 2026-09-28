"""Prefix rewrite + API shim for AstroAI Studio (dsh) CANFAR proxy."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "studio_canfar_proxy", ROOT / "studio-canfar-proxy.py"
)
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
    """ "/api/…" (file, present, mux) must get the session prefix for img/href."""
    proxy.PREFIX = "/session/contrib/abc"
    js = b'const FILE = "/api/file";const MUX = "/api/remote.mux";const OPEN = "/api/present.open";'
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
    assert b'"/open-in-app/"' in out  # dsh open-in-app plugin fetches outside /api
    assert b'id="astroai-terminal-chip"' in out
    assert b'href="/session/contrib/abc/terminal/"' in out
    assert b'id="astroai-agents-chip"' in out
    assert b'id="astroai-jupyter-chip"' in out
    assert b'href="/session/contrib/abc/jupyter/lab"' in out
    assert b'id="astroai-marimo-chip"' in out
    assert b'href="/session/contrib/abc/marimo/"' in out
    assert b'id="astroai-vscode-chip"' in out
    assert b'href="/session/contrib/abc/vscode/"' in out
    assert b"astroai-resource-banner" not in out
    assert b'data-astroai-proxy-rev="14"' in out
    assert b"data-astroai-tab" in out  # branded tab stick
    assert b"data-astroai-brand" in out  # boot splash wordmark
    assert out.index(b"data-astroai-brand") < out.index(b"</head>")


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


def test_rewrite_set_cookie_relaxes_samesite() -> None:
    raw = "dsh-auth-X=v1.abc; Max-Age=2592000; Path=/; HttpOnly; SameSite=Strict"
    out = proxy.rewrite_set_cookie(raw)
    assert "SameSite=Lax" in out
    assert "SameSite=Strict" not in out


def test_expire_dsh_auth_cookies() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    headers = proxy.expire_dsh_auth_cookies("dsh-auth-dead=v1.x; other=1; dsh-auth-dead=v1.x")
    assert any(h.startswith("dsh-auth-dead=; Max-Age=0; Path=/") for h in headers)
    assert any(
        h.startswith("dsh-auth-dead=; Max-Age=0; Path=/session/contrib/abc/") for h in headers
    )
    assert len(headers) == 2  # deduped name, two paths


def test_starting_html_is_refreshable() -> None:
    assert b'meta http-equiv="refresh"' in proxy.STARTING_HTML
    assert b"AstroAI Studio is starting" in proxy.STARTING_HTML


def test_get_studio_status() -> None:
    status = proxy.get_studio_status()
    assert "status" in status
    assert "services" in status
    assert "agent" in status["services"]
    assert "terminal" in status["services"]
    assert "jupyter" in status["services"]
    assert "marimo" in status["services"]
    assert "vscode" in status["services"]
    assert "hub" in status["services"]
    assert "resources" in status
    assert "scratch_free_gb" in status["resources"]


def test_command_dock_template() -> None:
    proxy.PREFIX = "/session/contrib/test123"
    dock = proxy.command_dock_html()
    assert "data-astroai-dock" in dock
    assert "/session/contrib/test123/jupyter/lab" in dock
    assert "/session/contrib/test123/marimo/" in dock
    assert "/session/contrib/test123/vscode/" in dock
    assert "/session/contrib/test123/terminal/" in dock
    assert 'data-tool="agent"' in dock
    assert 'data-mode="bar"' in dock
    assert "{prefix" not in dock and "{mode}" not in dock
    assert 'href="/session/contrib/test123/hub/#agents"' in dock
    assert 'href="/session/contrib/test123/hub/#compute"' in dock
    assert ">Assistant<" in dock and ">Agents<" in dock and ">Compute<" in dock
    # VS Code's CSP blocks inline scripts: the only script is the same-origin dock.js.
    import re as _re

    scripts = _re.findall(r"<script[^>]*>", dock)
    assert scripts == ['<script src="/session/contrib/test123/__studio/dock.js" defer>']
    assert not any(ch in dock for ch in "🤖💻🪐⚡📝🚀")


def test_studio_assets_are_branded() -> None:
    body, ctype = proxy.STUDIO_ASSETS["/__studio/dock.js"]
    assert ctype.startswith("text/javascript") and b"attachShadow" in body
    assert b"<svg" in proxy.STUDIO_ASSETS["/favicon.svg"][0]
    manifest = proxy.json.loads(proxy.STUDIO_ASSETS["/manifest.webmanifest"][0])
    assert manifest["name"] == "AstroAI Studio"


def test_assistant_bar_reserves_its_strip_and_nudges_for_a_key() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    out = proxy.rewrite_body(
        b"<html><head></head><body><div id=root></div></body></html>", "text/html"
    )
    # dsh's layout starts below the bar instead of under it (the session title stays visible).
    assert b"padding-top: 44px" in out and b"calc(100vh - 44px)" in out
    assert b".bar .dock {" in out and b"height: 44px" in out
    assert b"data-nokey hidden" in out
    assert b'href="/session/contrib/abc/hub/#agents" data-nokey' in out
    js = proxy.STUDIO_ASSETS["/__studio/dock.js"][0]
    assert b"'/hub/api/keys'" in js and b"k.dsh_route && k.present" in js
    # tool pages keep their own layout: no reserved strip there.
    assert b"padding-top" not in proxy.inject_dock(b"<html><body></body></html>", "text/html")


def test_vscode_gets_configuration_defaults(tmp_path: Path, monkeypatch) -> None:
    import html as _html

    settings = tmp_path / "settings.json"
    settings.write_text(
        '{"security.workspace.trust.enabled": false, "workbench.startupEditor": "none"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(proxy, "VSCODE_SETTINGS", str(settings))
    proxy.PREFIX = "/session/contrib/abc"
    cfg = {"serverBasePath": "/session/contrib/abc/vscode", "enableWorkspaceTrust": True}
    page = (
        '<html><head><meta id="vscode-workbench-web-configuration" data-settings="'
        + _html.escape(proxy.json.dumps(cfg), quote=True)
        + '"></head><body></body></html>'
    ).encode()
    out = proxy.inject_vscode(page, "text/html").decode()
    raw = proxy._VSCODE_WEB_CONFIG_RE.search(out).group(2)
    got = proxy.json.loads(_html.unescape(raw))
    assert got["enableWorkspaceTrust"] is False
    assert got["configurationDefaults"]["workbench.startupEditor"] == "none"
    assert got["serverBasePath"] == cfg["serverBasePath"]
    assert 'data-mode="mini"' in out
    assert proxy.inject_vscode(b"x", "text/javascript") == b"x"


def test_terminal_gets_corner_dock() -> None:
    proxy.PREFIX = "/session/contrib/abc"
    out = proxy.inject_corner_dock(b"<html><head></head><body></body></html>", "text/html")
    assert b'data-mode="corner"' in out


def test_inject_dock_leaves_tool_urls_alone() -> None:
    """Jupyter/marimo/vscode already serve under PREFIX: no rewrite, no dsh shim."""
    proxy.PREFIX = "/session/contrib/abc"
    html = (
        b'<html><head><base href="/session/contrib/abc/marimo/"></head>'
        b'<body><script>fetch("/api/home/recent_files")</script></body></html>'
    )
    out = proxy.inject_dock(html, "text/html; charset=utf-8")
    assert b'fetch("/api/home/recent_files")' in out
    assert b"data-astroai-api-shim" not in out
    assert b"data-astroai-dock" in out
    assert b'data-mode="mini"' in out
    assert out.index(b"data-astroai-dock") < out.index(b"</body>")
    js = b'fetch("/api/x")'
    assert proxy.inject_dock(js, "application/javascript") == js


def test_status_reports_workdir(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "studio-cwd").write_text("/scratch/src\n", encoding="utf-8")
    monkeypatch.setenv("ASTROAI_STUDIO_STATE", str(tmp_path))
    status = proxy.get_studio_status()
    assert status["workdir"] == "/scratch/src"
    assert status["workdir_ephemeral"] is True
    assert proxy._is_ephemeral("/srcdir")
    assert proxy._is_ephemeral("/arcade/x")
    assert not proxy._is_ephemeral("/arc/home/u/work")
    assert not proxy._is_ephemeral("/arc/projects/p")


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
