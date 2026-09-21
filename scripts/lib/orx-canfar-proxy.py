"""Reverse-proxy orx (or similar) for CANFAR contributed sessions.

orx serves a Vite SPA with absolute paths (``/assets/...``, ``/api/...``).
The browser URL is ``/session/contrib/<id>/`` while ingress strips that prefix
before the container. Absolute root paths therefore escape the session and the
UI stays blank (dark empty ``#root``).

This proxy:
  * listens on ``0.0.0.0:PUBLIC_PORT`` (default 5000)
  * forwards to ``127.0.0.1:ORX_PORT`` (default 4791)
  * routes ``/astroai-agents/*`` to the AstroAI agent wizard sidecar
  * routes ``/astroai-terminal/*`` to ghostty-web (WebSocket splice)
  * rewrites HTML/JS/CSS so absolute ``/api``, ``/assets``, ``/favicon``,
    ``/astroai-agents``, ``/astroai-terminal`` URLs include the session prefix
  * injects AstroAI + Terminal chips into HTML (proxy-only; no upstream fork)
"""

from __future__ import annotations

import contextlib
import json
import os
import select
import socket
import sys
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from session_title import stick_html_title  # noqa: E402

PUBLIC_PORT = int(os.environ.get("ASTROAI_OPENRESEARCH_PORT", "5000"))
ORX_HOST = os.environ.get("ORX_HOST", "127.0.0.1")
ORX_PORT = int(os.environ.get("ORX_PORT", "4791"))
WIZARD_HOST = os.environ.get("ASTROAI_AGENT_WIZARD_HOST", "127.0.0.1")
WIZARD_PORT = int(os.environ.get("ASTROAI_AGENT_WIZARD_PORT", "4792"))
TERMINAL_HOST = os.environ.get("ASTROAI_TERMINAL_HOST", "127.0.0.1")
TERMINAL_PORT = int(os.environ.get("ASTROAI_TERMINAL_PORT", "4793"))
SESSION_ID = (os.environ.get("skaha_sessionid") or "").strip()  # noqa: SIM112 — platform env var is lowercase
PREFIX = f"/session/contrib/{SESSION_ID}" if SESSION_ID else ""
WIZARD_MOUNT = "/astroai-agents"
TERMINAL_MOUNT = "/astroai-terminal"

REWRITE_TYPES = (
    "text/html",
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/x-javascript",
    "application/json",
)

# Absolute paths the SPA embeds that must stay under the contrib prefix.
ABS_PREFIXES = ("/api/", "/assets/", "/favicon", "/astroai-agents", "/astroai-terminal")

CHIP_STYLE = (
    "position:fixed;z-index:2147483646;padding:10px 14px;border-radius:8px;"
    "color:#fff;font:600 14px/1.2 system-ui,sans-serif;text-decoration:none;"
    "box-shadow:0 4px 16px rgba(0,0,0,.4)"
)

AGENTS_CHIP = (
    f'<a id="astroai-agents-chip" href="{{href}}" '
    f'style="{CHIP_STYLE};right:16px;top:16px;background:#3d8bfd;border:1px solid #5aa0ff">'
    "AstroAI</a>"
)

TERMINAL_CHIP = (
    f'<a id="astroai-terminal-chip" href="{{href}}" '
    f'style="{CHIP_STYLE};right:110px;top:16px;background:#1e3a2f;border:1px solid #3d6b54">'
    "Terminal</a>"
)


def rewrite_body(data: bytes, content_type: str) -> bytes:
    ctype = content_type.split(";", 1)[0].strip().lower()
    if ctype not in REWRITE_TYPES and not ctype.endswith("+json"):
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    if PREFIX:
        for abs_prefix in ABS_PREFIXES:
            # Avoid double-prefixing if somehow already rewritten.
            text = text.replace(f'"{PREFIX}{abs_prefix}', f'"__KEEP__{abs_prefix}')
            text = text.replace(f"'{PREFIX}{abs_prefix}", f"'__KEEP__{abs_prefix}")
            text = text.replace(f"`{PREFIX}{abs_prefix}", f"`__KEEP__{abs_prefix}")

            text = text.replace(f'"{abs_prefix}', f'"{PREFIX}{abs_prefix}')
            text = text.replace(f"'{abs_prefix}", f"'{PREFIX}{abs_prefix}")
            text = text.replace(f"`{abs_prefix}", f"`{PREFIX}{abs_prefix}")

            text = text.replace(f'"__KEEP__{abs_prefix}', f'"{PREFIX}{abs_prefix}')
            text = text.replace(f"'__KEEP__{abs_prefix}", f"'{PREFIX}{abs_prefix}")
            text = text.replace(f"`__KEEP__{abs_prefix}", f"`{PREFIX}{abs_prefix}")

        # TanStack Router (orx): without basepath, pathname /session/contrib/<id>/…
        # never matches routes (`/`, `/projects`, …) → in-app Not Found while chips
        # still show. trailingSlash always keeps a slash so Skaha ingress matches.
        # ponytail: string patch the baked createRouter call; upstream has no env for this.
        bp = json.dumps(PREFIX)
        if f"basepath:{bp}" not in text:
            if 'trailingSlash:"never"' in text:
                text = text.replace(
                    'trailingSlash:"never"',
                    f'basepath:{bp},trailingSlash:"always"',
                    1,
                )
            elif 'trailingSlash:"always"' in text:
                text = text.replace(
                    'trailingSlash:"always"',
                    f'basepath:{bp},trailingSlash:"always"',
                    1,
                )

    if ctype == "text/html":
        text = stick_html_title(text)
        if PREFIX:
            # Bust CDN/browser immutable cache of hashed assets (same content-hash
            # filename across sessions; upstream sends max-age=31536000, immutable).
            text = text.replace(".js\"", f'.js?astroai_bp=1"')
            text = text.replace(".js'", f".js?astroai_bp=1'")
            text = text.replace(".css\"", f'.css?astroai_bp=1"')
            text = text.replace(".css'", f".css?astroai_bp=1'")
        chips = ""
        if "astroai-terminal-chip" not in text:
            thref = f"{PREFIX}{TERMINAL_MOUNT}/" if PREFIX else f"{TERMINAL_MOUNT}/"
            chips += TERMINAL_CHIP.format(href=thref)
        if "astroai-agents-chip" not in text:
            href = f"{PREFIX}{WIZARD_MOUNT}/" if PREFIX else f"{WIZARD_MOUNT}/"
            chips += AGENTS_CHIP.format(href=href)
        if chips:
            lower = text.lower()
            idx = lower.rfind("</body>")
            text = text[:idx] + chips + text[idx:] if idx >= 0 else text + chips
    return text.encode("utf-8")


def rewrite_location(value: str) -> str:
    if not PREFIX or not value.startswith("/"):
        return value
    if value.startswith(PREFIX + "/") or value == PREFIX:
        return value
    for abs_prefix in ABS_PREFIXES:
        if value == abs_prefix.rstrip("/") or value.startswith(abs_prefix):
            return PREFIX + value
    if value.startswith(("/api", "/assets", WIZARD_MOUNT, TERMINAL_MOUNT)):
        return PREFIX + value
    return value


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


def is_websocket_request(handler: BaseHTTPRequestHandler) -> bool:
    conn = handler.headers.get("Connection", "").lower()
    upgrade = handler.headers.get("Upgrade", "").lower()
    return "upgrade" in conn and "websocket" in upgrade


def _splice_sockets(client: socket.socket, upstream: socket.socket) -> None:
    sockets = [client, upstream]
    try:
        while True:
            readable, _, _ = select.select(sockets, [], [], 300)
            if not readable:
                continue
            for src in readable:
                dst = upstream if src is client else client
                data = src.recv(65536)
                if not data:
                    return
                dst.sendall(data)
    except OSError:
        return


def forward_websocket(handler: BaseHTTPRequestHandler, host: str, port: int, path: str) -> None:
    try:
        upstream = socket.create_connection((host, port), timeout=30)
    except OSError as exc:
        handler.send_error(502, f"upstream unreachable: {exc}")
        return
    lines = [f"{handler.command} {path} HTTP/1.1"]
    for key, value in handler.headers.items():
        lines.append(f"{key}: {value}")
    payload = ("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1")
    try:
        upstream.sendall(payload)
        _splice_sockets(handler.connection, upstream)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            upstream.close()


def _forward(
    handler: BaseHTTPRequestHandler, host: str, port: int, path: str, *, rewrite: bool = True
) -> None:
    if is_websocket_request(handler):
        forward_websocket(handler, host, port, path)
        return

    accept = handler.headers.get("Accept", "")
    streaming = "text/event-stream" in accept or path.startswith("/api/events")

    headers = {
        k: v
        for k, v in handler.headers.items()
        if k.lower() not in HOP_BY_HOP
    }
    # Must rewrite uncompressed JS/HTML (basepath patch + /assets prefix).
    headers["Accept-Encoding"] = "identity"
    length = int(handler.headers.get("Content-Length", "0") or "0")
    body = handler.rfile.read(length) if length > 0 else None

    conn = HTTPConnection(host, port, timeout=600)
    try:
        conn.request(handler.command, path, body=body, headers=headers)
        upstream = conn.getresponse()
    except OSError as exc:
        if host == WIZARD_HOST and port == WIZARD_PORT:
            fallback = (
                b"<!DOCTYPE html><html><body style='font-family:sans-serif;padding:2rem'>"
                b"<h1>Agents unavailable</h1>"
                b"<p>Use terminal and run <code>astroai agent list --ui</code>.</p>"
                b"</body></html>"
            )
            handler.send_response(503)
            handler.send_header("Content-Type", "text/html; charset=utf-8")
            handler.send_header("Content-Length", str(len(fallback)))
            handler.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                handler.wfile.write(fallback)
            return
        if host == TERMINAL_HOST and port == TERMINAL_PORT:
            fallback = (
                b"<!DOCTYPE html><html><body style='font-family:sans-serif;padding:2rem'>"
                b"<h1>Terminal unavailable</h1>"
                b"<p>ghostty-web is not running in this session.</p>"
                b"</body></html>"
            )
            handler.send_response(503)
            handler.send_header("Content-Type", "text/html; charset=utf-8")
            handler.send_header("Content-Length", str(len(fallback)))
            handler.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                handler.wfile.write(fallback)
            return
        handler.send_error(502, f"upstream unreachable: {exc}")
        return

    content_type = upstream.getheader("Content-Type") or ""
    raw = b"" if streaming else upstream.read()
    original = raw
    if not streaming and rewrite:
        raw = rewrite_body(raw, content_type)
    rewritten = (not streaming) and rewrite and raw != original

    handler.send_response(upstream.status, upstream.reason)
    for key, value in upstream.getheaders():
        lk = key.lower()
        if lk in HOP_BY_HOP:
            continue
        if lk == "location":
            value = rewrite_location(value)
        if lk == "content-length" and not streaming:
            continue
        # Rewritten bodies must not inherit upstream immutable asset caching —
        # same content-hash filename would otherwise stick an unpatched bundle.
        if rewritten and lk in ("cache-control", "etag", "last-modified", "expires"):
            continue
        handler.send_header(key, value)
    if not streaming:
        handler.send_header("Content-Length", str(len(raw)))
    if rewritten:
        handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "close")
    handler.end_headers()

    if streaming:
        try:
            while True:
                chunk = upstream.read(8192)
                if not chunk:
                    break
                handler.wfile.write(chunk)
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
    else:
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            handler.wfile.write(raw)
    conn.close()


class OrxProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("orx-proxy: %s\n" % (fmt % args))

    def _proxy(self) -> None:
        path = self.path
        # Route AstroAI wizard under /astroai-agents (strip mount for sidecar).
        # Do not rewrite wizard HTML: prefixing quoted `/astroai-agents` in the
        # back-link script makes "Back" navigate to `/` and leave the session.
        if path == WIZARD_MOUNT or path.startswith(WIZARD_MOUNT + "/"):
            rest = path[len(WIZARD_MOUNT) :] or "/"
            _forward(self, WIZARD_HOST, WIZARD_PORT, rest, rewrite=False)
            return
        # ghostty-web under /astroai-terminal (relative ./client.mjs + /ws).
        if path == TERMINAL_MOUNT or path.startswith(TERMINAL_MOUNT + "/"):
            rest = path[len(TERMINAL_MOUNT) :] or "/"
            _forward(self, TERMINAL_HOST, TERMINAL_PORT, rest, rewrite=False)
            return
        _forward(self, ORX_HOST, ORX_PORT, path)

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
    server = ThreadingHTTPServer(("0.0.0.0", PUBLIC_PORT), OrxProxyHandler)
    sys.stderr.write(
        f"orx-proxy: listening 0.0.0.0:{PUBLIC_PORT} → {ORX_HOST}:{ORX_PORT} "
        f"wizard={WIZARD_HOST}:{WIZARD_PORT}{WIZARD_MOUNT} "
        f"terminal={TERMINAL_HOST}:{TERMINAL_PORT}{TERMINAL_MOUNT} "
        f"prefix={PREFIX or '(none)'}\n"
    )
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":
    _ = (select, socket, urlsplit)
    raise SystemExit(main())
