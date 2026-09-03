"""HTML rewrite + WebSocket splice for astroai-html-proxy."""

from __future__ import annotations

import importlib.util
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("astroai_html_proxy", ROOT / "astroai-html-proxy.py")
assert SPEC and SPEC.loader
proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proxy)


def test_rewrite_html_only() -> None:
    with patch("session_title.socket.gethostname", return_value="mysession"):
        html = proxy.rewrite_body(
            b"<html><head><title>marimo</title></head><body></body></html>",
            "text/html; charset=utf-8",
        )
        assert b"<title>mysession</title>" in html
        assert b"data-astroai-tab" in html
        js = proxy.rewrite_body(b"console.log('title')", "application/javascript")
        assert js == b"console.log('title')"


def test_is_websocket_request() -> None:
    handler = SimpleNamespace(
        headers={"Connection": "keep-alive, Upgrade", "Upgrade": "websocket"}
    )
    assert proxy.is_websocket_request(handler) is True
    handler.headers = {"Connection": "close", "Upgrade": ""}
    assert proxy.is_websocket_request(handler) is False


class _HtmlHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        pass

    def do_GET(self) -> None:
        body = b"<html><head><title>marimo</title></head><body>ok</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_http_proxy_rewrites_html() -> None:
    backend = ThreadingHTTPServer(("127.0.0.1", 0), _HtmlHandler)
    bt = threading.Thread(target=backend.serve_forever, daemon=True)
    bt.start()
    proxy.BACKEND_HOST = "127.0.0.1"
    proxy.BACKEND_PORT = backend.server_address[1]
    front = ThreadingHTTPServer(("127.0.0.1", 0), proxy.ProxyHandler)
    ft = threading.Thread(target=front.serve_forever, daemon=True)
    ft.start()
    try:
        with patch("session_title.socket.gethostname", return_value="tabname"):
            with urlopen(f"http://127.0.0.1:{front.server_address[1]}/", timeout=5) as resp:
                body = resp.read()
        assert b"<title>tabname</title>" in body
        assert b"data-astroai-tab" in body
    finally:
        front.shutdown()
        backend.shutdown()


def _ws_backend(sock: socket.socket) -> None:
    conn, _ = sock.accept()
    try:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                return
            data += chunk
        assert b"Upgrade: websocket" in data
        conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n")
        echo = conn.recv(64)
        conn.sendall(echo)
    finally:
        conn.close()
        sock.close()


def test_websocket_splice() -> None:
    backend_sock = socket.socket()
    backend_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    backend_sock.bind(("127.0.0.1", 0))
    backend_sock.listen(1)
    backend_port = backend_sock.getsockname()[1]
    threading.Thread(target=_ws_backend, args=(backend_sock,), daemon=True).start()

    proxy.BACKEND_HOST = "127.0.0.1"
    proxy.BACKEND_PORT = backend_port
    front = ThreadingHTTPServer(("127.0.0.1", 0), proxy.ProxyHandler)
    threading.Thread(target=front.serve_forever, daemon=True).start()
    try:
        client = socket.create_connection(("127.0.0.1", front.server_address[1]), timeout=5)
        client.sendall(
            b"GET /ws HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Connection: Upgrade\r\n"
            b"Upgrade: websocket\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 13\r\n"
            b"\r\n"
        )
        handshake = b""
        while b"\r\n\r\n" not in handshake:
            chunk = client.recv(4096)
            assert chunk, "proxy closed during websocket handshake"
            handshake += chunk
        assert handshake.startswith(b"HTTP/1.1 101")
        client.sendall(b"ping-ws")
        assert client.recv(64) == b"ping-ws"
        client.close()
    finally:
        front.shutdown()


if __name__ == "__main__":
    test_rewrite_html_only()
    test_is_websocket_request()
    test_http_proxy_rewrites_html()
    test_websocket_splice()
    print("ok")
