"""Reverse proxy for contributed SPAs: stick CANFAR session name in the browser tab.

Listens on ``ASTROAI_PUBLIC_PORT`` (default 5000) and forwards to
``ASTROAI_BACKEND_HOST:ASTROAI_BACKEND_PORT``. HTML responses get
``stick_html_title`` so SPAs (marimo, etc.) do not revert to a generic name.

Used when the app has no native title setting (marimo). OpenResearch uses
``orx-canfar-proxy.py`` which also rewrites absolute asset paths.
"""

from __future__ import annotations

import contextlib
import os
import sys
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from session_title import stick_html_title  # noqa: E402

PUBLIC_PORT = int(os.environ.get("ASTROAI_PUBLIC_PORT", "5000"))
BACKEND_HOST = os.environ.get("ASTROAI_BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.environ.get("ASTROAI_BACKEND_PORT", "8765"))
FALLBACK = os.environ.get("ASTROAI_TAB_FALLBACK", "AstroAI")

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}


def rewrite_body(data: bytes, content_type: str) -> bytes:
    ctype = content_type.split(";", 1)[0].strip().lower()
    if ctype != "text/html":
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return stick_html_title(text, FALLBACK).encode("utf-8")


def _forward(handler: BaseHTTPRequestHandler, path: str) -> None:
    headers = {k: v for k, v in handler.headers.items() if k.lower() not in HOP_BY_HOP}
    length = int(handler.headers.get("Content-Length", "0") or "0")
    body = handler.rfile.read(length) if length > 0 else None

    conn = HTTPConnection(BACKEND_HOST, BACKEND_PORT, timeout=600)
    try:
        conn.request(handler.command, path, body=body, headers=headers)
        upstream = conn.getresponse()
    except OSError as exc:
        handler.send_error(502, f"upstream unreachable: {exc}")
        return

    content_type = upstream.getheader("Content-Type") or ""
    raw = upstream.read()
    raw = rewrite_body(raw, content_type)

    handler.send_response(upstream.status, upstream.reason)
    for key, value in upstream.getheaders():
        lk = key.lower()
        if lk in HOP_BY_HOP:
            continue
        if lk == "content-length":
            continue
        handler.send_header(key, value)
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
        handler.wfile.write(raw)
    conn.close()


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("astroai-html-proxy: %s\n" % (fmt % args))

    def _proxy(self) -> None:
        _forward(self, self.path)

    def do_GET(self) -> None:
        self._proxy()

    def do_POST(self) -> None:
        self._proxy()

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy()

    def do_PATCH(self) -> None:  # noqa: N802
        self._proxy()

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy()

    def do_HEAD(self) -> None:  # noqa: N802
        self._proxy()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._proxy()


def main() -> int:
    server = ThreadingHTTPServer(("0.0.0.0", PUBLIC_PORT), ProxyHandler)
    sys.stderr.write(
        f"astroai-html-proxy: 0.0.0.0:{PUBLIC_PORT} → {BACKEND_HOST}:{BACKEND_PORT} "
        f"fallback={FALLBACK!r}\n"
    )
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
