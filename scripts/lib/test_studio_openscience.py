"""/openscience/ route of the Studio proxy: HTML rewrite, loopback headers, lazy start."""

from __future__ import annotations

import importlib.util
import json
import socket
import threading
import time
from collections.abc import Iterator
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "studio_canfar_proxy_os", ROOT / "studio-canfar-proxy.py"
)
assert SPEC and SPEC.loader
proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proxy)

PREFIX = "/session/contrib/abc"
TOKEN = "t0ken-for-tests"
INDEX = (
    b'<!doctype html><html lang="en"><head><meta charset="utf-8" />'
    b'<script src="./openscience-theme-preload.js"></script>'
    b'<script type="module" crossorigin src="./assets/index-X.js"></script>'
    b'<link rel="stylesheet" href="./assets/index-X.css"></head>'
    b'<body><div id="root"></div></body></html>'
)


@pytest.fixture(autouse=True)
def _prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy, "PREFIX", PREFIX)


def test_index_gets_absolute_assets_base_and_dock() -> None:
    out = proxy.inject_openscience(INDEX, "text/html; charset=utf-8").decode()
    assert f'src="{PREFIX}/openscience/assets/index-X.js"' in out
    assert f'href="{PREFIX}/openscience/assets/index-X.css"' in out
    assert f'src="{PREFIX}/openscience/openscience-theme-preload.js"' in out
    assert '="./' not in out
    # OpenScience's CSP is script-src 'self': the base path must come from a file.
    boot = f'<script data-astroai-openscience src="{PREFIX}/openscience/__astroai/boot.js">'
    assert boot in out
    assert out.index(boot) < out.index("openscience-theme-preload.js")
    assert "window.__OPENSCIENCE" not in out
    assert 'data-mode="mini"' in out
    assert b"./assets" in proxy.inject_openscience(b'import("./assets/a.js")', "text/javascript")


def test_dock_links_to_openscience() -> None:
    dock = proxy.command_dock_html("bar")
    assert 'id="astroai-openscience-chip"' in dock
    assert f'href="{PREFIX}/openscience/"' in dock


def test_headers_become_loopback_with_token() -> None:
    items = [
        ("Host", "ws-uv.canfar.net"),
        ("Origin", "https://ws-uv.canfar.net"),
        ("Referer", f"https://ws-uv.canfar.net{PREFIX}/openscience/"),
        ("Cookie", "CADC_SSO=secret"),
        ("Authorization", "Bearer from-browser"),
        ("X-Forwarded-For", "1.2.3.4"),
        ("X-Real-IP", "1.2.3.4"),
        ("Accept-Encoding", "gzip"),
        ("Connection", "keep-alive"),
        ("Content-Type", "application/json"),
    ]
    out = dict(proxy.openscience_headers(items, TOKEN))
    assert out["Host"] == "127.0.0.1:4796"
    assert out["Origin"] == "http://127.0.0.1:4796"
    assert out["Authorization"] == f"Bearer {TOKEN}"
    assert out["Accept-Encoding"] == "identity"
    assert out["Content-Type"] == "application/json"
    for gone in ("Cookie", "Referer", "X-Forwarded-For", "X-Real-IP", "Connection"):
        assert gone not in out
    ws = dict(
        proxy.openscience_headers(
            [("Connection", "Upgrade"), ("Upgrade", "websocket"), ("Sec-WebSocket-Key", "k")],
            TOKEN,
            websocket=True,
        )
    )
    assert ws["Upgrade"] == "websocket"
    assert ws["Connection"] == "Upgrade"
    assert ws["Sec-WebSocket-Key"] == "k"
    assert ws["Authorization"] == f"Bearer {TOKEN}"


def test_redirects_stay_under_mount() -> None:
    assert proxy.rewrite_openscience_location("/x?y=1") == f"{PREFIX}/openscience/x?y=1"
    assert proxy.rewrite_openscience_location("https://e.org/a") == "https://e.org/a"
    assert proxy.rewrite_openscience_location("//e.org/a") == "//e.org/a"


class FakeOpenScience(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen: list[dict[str, str]] = []

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        FakeOpenScience.seen.append({"path": self.path, **dict(self.headers.items())})
        if self.headers.get("Host") != "127.0.0.1:" + str(self.server.server_port):
            return self._send(403, b'{"error":"Forbidden host"}', "application/json")
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, b"{}", "application/json")
        if self.path == "/global/event":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for n in range(2):
                data = f"data: {n}\n\n".encode()
                self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                self.wfile.flush()
                time.sleep(1.0)
            self.wfile.write(b"0\r\n\r\n")
            return None
        if self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "/session/xyz")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        return self._send(200, INDEX, "text/html; charset=utf-8")

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve(handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def studio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    state = tmp_path / "state"
    state.mkdir()
    (state / "openscience-token").write_text(TOKEN + "\n")
    monkeypatch.setenv("ASTROAI_STUDIO_STATE", str(state))
    upstream = _serve(FakeOpenScience)
    monkeypatch.setattr(proxy, "OPENSCIENCE_PORT", upstream.server_port)
    front = _serve(proxy.StudioProxyHandler)
    FakeOpenScience.seen.clear()
    yield {"state": state, "upstream": upstream, "port": front.server_port}
    front.shutdown()
    upstream.shutdown()


def _get(port: int, path: str, **headers: str) -> tuple[int, dict[str, str], bytes]:
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path, headers={"Host": "ws-uv.canfar.net", **headers})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, {k.lower(): v for k, v in resp.getheaders()}, body


def test_deep_link_is_proxied_with_token_and_rewritten(studio: dict) -> None:
    status, _, body = _get(
        studio["port"],
        f"{PREFIX}/openscience/project/abc/files?x=1",
        Origin="https://ws-uv.canfar.net",
        Cookie="CADC_SSO=secret",
    )
    assert status == 200
    assert f'src="{PREFIX}/openscience/assets/index-X.js"'.encode() in body
    seen = FakeOpenScience.seen[-1]
    assert seen["path"] == "/project/abc/files?x=1"
    assert seen["Origin"] == f"http://127.0.0.1:{studio['upstream'].server_port}"
    assert "Cookie" not in seen


def test_boot_script_is_served_even_before_upstream_starts(studio: dict) -> None:
    studio["upstream"].shutdown()
    studio["upstream"].server_close()
    status, headers, body = _get(studio["port"], f"{PREFIX}/openscience/__astroai/boot.js")
    assert status == 200
    assert headers["content-type"].startswith("text/javascript")
    assert body.decode() == (
        f'window.__OPENSCIENCE_BASE_URL__="{PREFIX}/openscience";'
        "window.__OPENSCIENCE_TRUSTED_PROXY__=true;\n"
    )


def test_upstream_redirect_and_bare_mount(studio: dict) -> None:
    status, headers, _ = _get(studio["port"], f"{PREFIX}/openscience/moved")
    assert status == 302
    assert headers["location"] == f"{PREFIX}/openscience/session/xyz"
    status, headers, _ = _get(studio["port"], f"{PREFIX}/openscience")
    assert status == 302
    assert headers["location"] == f"{PREFIX}/openscience/"


def test_event_stream_is_not_buffered(studio: dict) -> None:
    conn = HTTPConnection("127.0.0.1", studio["port"], timeout=10)
    start = time.monotonic()
    conn.request("GET", f"{PREFIX}/openscience/global/event", headers={"Accept": "*/*"})
    resp = conn.getresponse()
    assert resp.getheader("X-Accel-Buffering") == "no"
    first = resp.read1(64)
    elapsed = time.monotonic() - start
    conn.close()
    assert first.startswith(b"data: 0")
    assert elapsed < 0.8, f"first event after {elapsed:.2f}s: buffered until the next one"


def test_lazy_start_page_then_failure_and_retry(studio: dict) -> None:
    studio["upstream"].shutdown()
    studio["upstream"].server_close()
    state: Path = studio["state"]
    status, _, body = _get(studio["port"], f"{PREFIX}/openscience/a?b=1", Accept="text/html")
    assert status == 200
    assert b"OpenScience is starting" in body
    assert f'url={PREFIX}/openscience/a?b=1"'.encode() in body
    assert (state / "openscience.want").exists()
    status, _, body = _get(studio["port"], f"{PREFIX}/openscience/global/health")
    assert status == 503
    assert json.loads(body) == {"error": "starting"}

    (state / "openscience.want").unlink()
    (state / "openscience.failed").touch()
    status, _, body = _get(studio["port"], f"{PREFIX}/openscience/", Accept="text/html")
    assert status == 503
    assert b"could not start" in body
    assert b"astroai-retry=1" in body
    assert not (state / "openscience.want").exists()
    status, _, body = _get(
        studio["port"], f"{PREFIX}/openscience/?astroai-retry=1", Accept="text/html"
    )
    assert status == 200
    assert not (state / "openscience.failed").exists()
    assert (state / "openscience.want").exists()
    assert b"astroai-retry" not in body.split(b"<body", 1)[0]


def test_websocket_handshake_carries_loopback_headers(studio: dict, monkeypatch) -> None:
    captured: dict[str, bytes] = {}
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def accept() -> None:
        # The proxy's port probe connects first and sends nothing.
        while "request" not in captured:
            conn, _ = listener.accept()
            data = conn.recv(65536)
            if data:
                captured["request"] = data
                conn.sendall(
                    b"HTTP/1.1 101 Switching Protocols\r\n"
                    b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
                )
            conn.close()

    threading.Thread(target=accept, daemon=True).start()
    monkeypatch.setattr(proxy, "OPENSCIENCE_PORT", listener.getsockname()[1])
    client = socket.create_connection(("127.0.0.1", studio["port"]), timeout=5)
    client.sendall(
        (
            f"GET {PREFIX}/openscience/pty/p1/connect?cursor=0 HTTP/1.1\r\n"
            "Host: ws-uv.canfar.net\r\nOrigin: https://ws-uv.canfar.net\r\n"
            "Cookie: CADC_SSO=secret\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n"
            "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
        ).encode()
    )
    reply = client.recv(4096)
    client.close()
    listener.close()
    assert reply.startswith(b"HTTP/1.1 101")
    req = captured["request"].decode()
    assert req.startswith("GET /pty/p1/connect?cursor=0 HTTP/1.1\r\n")
    assert f"Host: 127.0.0.1:{proxy.OPENSCIENCE_PORT}" in req
    assert f"Origin: http://127.0.0.1:{proxy.OPENSCIENCE_PORT}" in req
    assert f"Authorization: Bearer {TOKEN}" in req
    assert "Upgrade: websocket" in req
    assert "CADC_SSO" not in req


def test_status_lists_openscience_on_demand() -> None:
    svc = proxy.get_studio_status()["services"]["openscience"]
    assert svc["on_demand"] is True
    assert svc["port"] == proxy.OPENSCIENCE_PORT
