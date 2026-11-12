"""astroai/dsh proxy: routes, Lab auth/prefix, SSE, Connect token, tab strip.

Runs fake dsh and Lab upstreams on loopback and the real handler in front.
Docker-free; ``python3 scripts/lib/test_dsh_canfar_proxy.py``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import tempfile
import threading
import time
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SESSION = "abc123"
P = f"/session/contrib/{SESSION}"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


SEEN: dict[str, dict[str, str]] = {}


class FakeDsh(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        SEEN["dsh"] = {"path": self.path, **dict(self.headers.items())}
        if self.path.startswith("/api/doc"):
            body, ctype = b'{"content": "fetch(\\"/api/x\\")"}', "application/json"
        elif self.path.startswith("/api/file"):
            body, ctype = b"<html><head></head><body>user file</body></html>", "text/html"
        elif self.path.startswith("/?stale"):
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        else:
            body = (
                b"<html><head><title>x</title></head>"
                b'<body><script src="/assets/a.js"></script></body></html>'
            )
            ctype = "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Set-Cookie", "dsh-auth-1=v; Path=/; SameSite=Strict")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeLab(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a) -> None:
        pass

    def _reply(self) -> None:
        SEEN["lab"] = {"path": self.path, "method": self.command, **dict(self.headers.items())}
        if self.path == "/api/jobs/1/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")  # as canfar_lab.web.server does
            self.end_headers()
            self.close_connection = True
            for i in range(3):
                self.wfile.write(f"data: line{i}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.05)
            return
        if self.path == "/moved":
            self.send_response(303)
            self.send_header("Location", "/api/state")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/":
            body, ctype = b"<html><head></head><body>lab</body></html>", "text/html"
        else:
            body = json.dumps({"pending": 2, "ready": False, "kind": "new"}).encode()
            ctype = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_HEAD = _reply  # noqa: N815


def _serve(handler: type, port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _load_proxy(dsh_port: int, lab_port: int, token_file: Path):
    tabs = Path(tempfile.mkdtemp()) / "tabs.json"
    cfg = json.loads((ROOT.parent.parent / "config" / "dsh-tabs.json").read_text())
    tabs.write_text(json.dumps(cfg))
    os.environ.update(
        {
            "skaha_sessionid": SESSION,
            "ASTROAI_TABS_FILE": str(tabs),
            "DSH_PORT": str(dsh_port),
            "CANFAR_LAB_WEB_PORT": str(lab_port),
            "CANFAR_LAB_WEB_TOKEN": "labsecret",
            "ASTROAI_DSH_TOKEN_FILE": str(token_file),
            # On-demand tools: ports nothing listens on, and a state dir for requests.
            "ASTROAI_TERMINAL_PORT": str(_free_port()),
            "ASTROAI_JUPYTER_PORT": str(_free_port()),
            "ASTROAI_VSCODE_PORT": str(_free_port()),
            "ASTROAI_STUDIO_STATE": STATE_DIR,
        }
    )
    spec = importlib.util.spec_from_file_location("dsh_canfar_proxy", ROOT / "dsh-canfar-proxy.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


STATE_DIR = tempfile.mkdtemp()
DSH_PORT, LAB_PORT, PROXY_PORT = _free_port(), _free_port(), _free_port()
TOKEN_FILE = Path(tempfile.mkdtemp()) / "dsh-web-token"
proxy = _load_proxy(DSH_PORT, LAB_PORT, TOKEN_FILE)
LAB_TAB = next(t for t in proxy.TABS if t["id"] == "lab")


def _get(path: str, headers: dict[str, str] | None = None, method: str = "GET", body=None):
    conn = HTTPConnection("127.0.0.1", PROXY_PORT, timeout=10)
    conn.request(
        method, path, body=body, headers={"Host": "workloads.canfar.net", **(headers or {})}
    )
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp, data


def test_tab_table_and_matching() -> None:
    ids = [t["id"] for t in proxy.TABS]
    assert ids == ["agent", "terminal", "jupyter", "vscode", "lab"], ids
    starts = {t["id"] for t in proxy.TABS if t["start"]}
    assert starts == {"terminal", "jupyter", "vscode"}
    assert [t["id"] for t in proxy.TABS if t["align"] == "end"] == ["lab"]
    assert proxy.match_tab("/terminal/ws")["id"] == "terminal"
    assert not next(t for t in proxy.TABS if t["id"] == "jupyter")["strip"]
    assert proxy.match_tab("/lab/api/state")["id"] == "lab"
    assert proxy.match_tab("/lab")["id"] == "lab"
    assert proxy.match_tab("/laboratory")["id"] == "agent"
    assert proxy.match_tab("/api/x")["id"] == "agent"


def test_bad_tab_file_falls_back(tmp: Path | None = None) -> None:
    bad = Path(tempfile.mkdtemp()) / "t.json"
    bad.write_text('{"tabs": [{"prefix": "no-slash", "port": 1}]}')
    assert [t["id"] for t in proxy.load_tabs(str(bad))] == ["agent", "lab"]


def test_doubled_session_prefix_collapses() -> None:
    assert proxy.upstream_path(f"{P}{P}/api/remote.mux?x=1") == "/api/remote.mux?x=1"
    assert proxy.upstream_path(f"{P}/lab/") == "/lab/"
    assert proxy.upstream_path("/elsewhere") == "/elsewhere"
    resp, _ = _get(f"{P}{P}/lab/api/state")
    assert resp.status == 200 and SEEN["lab"]["path"] == "/api/state"


def test_status_endpoint() -> None:
    resp, data = _get(f"{P}/api/studio/status")
    assert resp.status == 200
    doc = json.loads(data)
    assert doc["status"] == "ready" and doc["services"]["lab"]["up"] is True


def test_starting_page_before_dsh_token() -> None:
    TOKEN_FILE.unlink(missing_ok=True)
    resp, data = _get(f"{P}/")
    assert resp.status == 200 and b"starting" in data


def test_connect_token_redirect() -> None:
    TOKEN_FILE.write_text("tok123\n")
    resp, _ = _get(f"{P}/")
    assert resp.status == 302
    assert resp.getheader("Location") == f"{P}/?token=tok123"


def test_dsh_page_rewritten_with_tabs() -> None:
    TOKEN_FILE.write_text("tok123\n")
    resp, data = _get(
        f"{P}/", {"Cookie": "dsh-auth-1=v; astroai-onboarded=1", "Sec-Fetch-Dest": "document"}
    )
    assert resp.status == 200
    assert f'"{P}/assets/a.js"'.encode() in data
    assert b"data-astroai-api-shim" in data
    assert b'id="astroai-tabs-config"' in data
    cfg = json.loads(data.split(b'type="application/json">')[1].split(b"</script>")[0])
    assert cfg["active"] == "agent"
    assert [t["href"] for t in cfg["tabs"]] == [
        f"{P}/",
        f"{P}/terminal/",
        f"{P}/jupyter/",
        f"{P}/vscode/",
        f"{P}/lab/",
    ]
    lab = cfg["tabs"][-1]
    assert lab["badge"] == "api/onboarding" and lab["end"] is True
    assert f'src="{P}/__dsh/tabs.js"'.encode() in data
    assert "SameSite=Lax" in resp.getheader("Set-Cookie")
    assert SEEN["dsh"]["Host"] == "workloads.canfar.net"
    assert "X-Forwarded-Prefix" not in SEEN["dsh"]


def test_brand_new_session_opens_the_checklist_once() -> None:
    TOKEN_FILE.write_text("tok123\n")
    doc = {"Sec-Fetch-Dest": "document"}
    resp, _ = _get(f"{P}/", {"Cookie": "dsh-auth-1=v", **doc})
    assert resp.status == 302 and resp.getheader("Location") == f"{P}/lab/"
    cookie = resp.getheader("Set-Cookie")
    assert cookie.startswith("astroai-onboarded=1") and f"Path={P}/" in cookie
    assert SEEN["lab"]["path"] == "/api/onboarding"
    assert SEEN["lab"]["Authorization"] == "Bearer labsecret"
    resp, data = _get(f"{P}/", {"Cookie": "dsh-auth-1=v; astroai-onboarded=1", **doc})
    assert resp.status == 200 and b"astroai-tabs-config" in data
    # Not for API calls or iframes, and never before dsh's own login.
    resp, _ = _get(f"{P}/", {"Cookie": "dsh-auth-1=v", "Sec-Fetch-Dest": "iframe"})
    assert resp.status == 200


def test_no_tabs_in_iframes_or_user_files() -> None:
    _, data = _get(f"{P}/api/file?p=x.html", {"Cookie": "dsh-auth-1=v", "Sec-Fetch-Dest": "iframe"})
    assert b"astroai-tabs-config" not in data
    _, data = _get(f"{P}/api/file?p=x.html", {"Cookie": "dsh-auth-1=v"})
    assert b"astroai-tabs-config" not in data


def test_stale_dsh_cookie_recovers() -> None:
    TOKEN_FILE.write_text("tok123\n")
    resp, _ = _get(f"{P}/?stale=1", {"Cookie": "dsh-auth-1=old; astroai-onboarded=1"})
    assert resp.status == 302, resp.status
    assert "token=tok123" in resp.getheader("Location")
    assert any("Max-Age=0" in v for k, v in resp.getheaders() if k.lower() == "set-cookie")


def test_lab_redirect_and_page() -> None:
    resp, _ = _get(f"{P}/lab")
    assert resp.status == 302 and resp.getheader("Location") == f"{P}/lab/"
    resp, data = _get(f"{P}/lab/", {"Sec-Fetch-Dest": "document", "Authorization": "Bearer evil"})
    assert resp.status == 200 and b"lab" in data
    assert SEEN["lab"]["path"] == "/"
    assert SEEN["lab"]["Authorization"] == "Bearer labsecret"
    assert SEEN["lab"]["X-Forwarded-Prefix"] == f"{P}/lab"
    cfg = json.loads(data.split(b'type="application/json">')[1].split(b"</script>")[0])
    assert cfg["active"] == "lab"
    assert b"data-astroai-api-shim" not in data  # Lab uses relative URLs + <base>


def test_lab_api_and_write_header_pass_through() -> None:
    resp, data = _get(
        f"{P}/lab/api/agents/opencode/install",
        {"X-Canfar-Lab": "1", "Content-Type": "application/json"},
        method="POST",
        body=b"{}",
    )
    assert resp.status == 200
    assert SEEN["lab"]["path"] == "/api/agents/opencode/install"
    assert SEEN["lab"]["X-Canfar-Lab"] == "1"
    assert b"astroai-tabs-config" not in data


def test_lab_sse_streams_unbuffered() -> None:
    conn = HTTPConnection("127.0.0.1", PROXY_PORT, timeout=10)
    conn.request("GET", f"{P}/lab/api/jobs/1/events", headers={"Accept": "text/event-stream"})
    resp = conn.getresponse()
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "text/event-stream"
    assert resp.getheader("Content-Length") is None
    first = resp.fp.readline()
    assert first == b"data: line0\n", first
    rest = resp.read()
    assert b"line2" in rest
    conn.close()


def test_dsh_json_is_never_rewritten() -> None:
    _, data = _get(f"{P}/api/doc", {"Cookie": "dsh-auth-1=v"})
    assert data == b'{"content": "fetch(\\"/api/x\\")"}', data


def test_head_passes_upstream_length_untouched() -> None:
    resp, data = _get(f"{P}/lab/", {"Sec-Fetch-Dest": "document"}, method="HEAD")
    assert resp.status == 200 and data == b""
    assert resp.getheader("Content-Length") == str(
        len(b"<html><head></head><body>lab</body></html>")
    )


def test_lab_absolute_redirect_stays_under_session() -> None:
    resp, _ = _get(f"{P}/lab/moved")
    assert resp.getheader("Location") == f"{P}/lab/api/state"


def test_request_body_framing() -> None:
    resp, _ = _get(
        f"{P}/lab/api/x", {"Transfer-Encoding": "chunked"}, method="POST", body=b"0\r\n\r\n"
    )
    assert resp.status == 411
    conn = HTTPConnection("127.0.0.1", PROXY_PORT, timeout=10)
    conn.putrequest("POST", f"{P}/lab/api/x")
    conn.putheader("Content-Length", "-5")
    conn.endheaders()
    assert conn.getresponse().status == 400
    conn.close()


def test_early_redirect_closes_connection() -> None:
    resp, _ = _get(f"{P}/lab", method="POST", body=b"leftover-body")
    assert resp.status == 302 and resp.getheader("Connection") == "close"


def test_opening_a_stopped_tool_requests_its_start() -> None:
    request = Path(STATE_DIR) / "tools" / "jupyter"
    request.unlink(missing_ok=True)
    resp, data = _get(f"{P}/jupyter/lab", {"Sec-Fetch-Dest": "document"})
    assert resp.status == 503 and b"Opening JupyterLab" in data
    assert b"first time" in data and b"astroai-tabs-config" in data
    assert request.exists()
    # A tool without "start" (the Lab) never drops a request.
    saved = LAB_TAB["port"]
    LAB_TAB["port"] = _free_port()
    try:
        _get(f"{P}/lab/")
        assert not (Path(STATE_DIR) / "tools" / "lab").exists()
    finally:
        LAB_TAB["port"] = saved


def test_unstripped_tool_keeps_its_base_path() -> None:
    """JupyterLab serves under /session/contrib/<id>/jupyter itself."""
    seen: dict[str, str] = {}

    class FakeJupyter(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            seen["path"] = self.path
            seen["prefix"] = self.headers.get("X-Forwarded-Prefix") or ""
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    tab = next(t for t in proxy.TABS if t["id"] == "jupyter")
    port = _free_port()
    srv = _serve(FakeJupyter, port)
    saved, tab["port"] = tab["port"], port
    try:
        resp, _ = _get(f"{P}/jupyter/api/status")
        assert resp.status == 200 and seen["path"] == f"{P}/jupyter/api/status"
        assert seen["prefix"] == ""
    finally:
        tab["port"] = saved
        srv.shutdown()


def test_html_navigation_without_fetch_metadata_gets_tabs() -> None:
    """Plain-http origins get no Sec-Fetch-*; a request asking for HTML is a page view."""
    page = {
        "Accept": "text/html,application/xhtml+xml",
        "Cookie": "dsh-auth-1=v; astroai-onboarded=1",
    }
    _, data = _get(f"{P}/deep/route", page)
    assert b"astroai-tabs-config" in data
    _, data = _get(f"{P}/api/file?p=x.html", {"Accept": "*/*", "Cookie": "dsh-auth-1=v"})
    assert b"astroai-tabs-config" not in data


def test_vscode_trust_patch() -> None:
    settings = Path(tempfile.mkdtemp()) / "settings.json"
    settings.write_text(
        json.dumps({"security.workspace.trust.enabled": False, "editor.fontSize": 14})
    )
    proxy.VSCODE_SETTINGS = str(settings)
    cfg = json.dumps({"remoteAuthority": "x", "configurationDefaults": {"a": 1}})
    page = (
        '<html><head><meta id="vscode-workbench-web-configuration" data-settings="'
        + proxy.html.escape(cfg, quote=True)
        + '"></head><body></body></html>'
    ).encode()
    out = proxy.patch_vscode(page, "text/html").decode()
    raw = out.split('data-settings="')[1].split('"')[0]
    patched = json.loads(proxy.html.unescape(raw))
    assert patched["enableWorkspaceTrust"] is False
    assert patched["configurationDefaults"] == {
        "a": 1,
        "security.workspace.trust.enabled": False,
        "editor.fontSize": 14,
    }
    assert proxy.patch_vscode(b"{}", "application/json") == b"{}"


def test_tabs_js_served() -> None:
    resp, data = _get(f"{P}/__dsh/tabs.js")
    assert resp.status == 200 and b"attachShadow" in data


def test_lab_down_says_starting() -> None:
    saved = LAB_TAB["port"]
    LAB_TAB["port"] = _free_port()
    try:
        resp, data = _get(f"{P}/lab/")
        assert resp.status == 503 and b"Opening Lab" in data
    finally:
        LAB_TAB["port"] = saved


if __name__ == "__main__":
    _serve(FakeDsh, DSH_PORT)
    _serve(FakeLab, LAB_PORT)
    srv = ThreadingHTTPServer(("127.0.0.1", PROXY_PORT), proxy.DshProxyHandler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok {name}")
    print(f"{len(tests)} passed")
