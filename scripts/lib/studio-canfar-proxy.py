"""Reverse-proxy dsh web for CANFAR contributed Studio sessions.

dsh builds every RPC/WebSocket URL as ``new URL('/api/…', location.origin)``,
so absolute ``/api`` escapes ``/session/contrib/<id>/``. Rewriting the string
``\"/api\"`` in bundles to a multi-segment path also breaks dsh's
``CHANNEL_PATTERN`` (single-segment channels only).

This proxy:
  * listens on ``0.0.0.0:PUBLIC_PORT`` (default 5000)
  * forwards to ``127.0.0.1:DSH_PORT`` (default 3080)
  * splices WebSocket upgrades (dsh ``/api/remote.mux``)
  * injects an early fetch/WebSocket shim that prefixes ``/api`` with the
    session path (channel string stays ``/api`` for client validation)
  * rewrites ``/assets`` / favicon / hub links the same way as orx
  * forwards browser ``Host`` + ``Origin`` so dsh's trust fence can match
    (start dsh with ``--trusted-host <public-host>``)
  * routes ``/astroai-agents/*`` to the AstroAI agent wizard sidecar
"""

from __future__ import annotations

import contextlib
import os
import select
import socket
import sys
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from session_title import stick_html_title  # noqa: E402

PUBLIC_PORT = int(os.environ.get("ASTROAI_STUDIO_PORT", "5000"))
DSH_HOST = os.environ.get("DSH_HOST", "127.0.0.1")
DSH_PORT = int(os.environ.get("DSH_PORT", "3080"))
WIZARD_HOST = os.environ.get("ASTROAI_AGENT_WIZARD_HOST", "127.0.0.1")
WIZARD_PORT = int(os.environ.get("ASTROAI_AGENT_WIZARD_PORT", "4792"))
SESSION_ID = (os.environ.get("skaha_sessionid") or "").strip()  # noqa: SIM112
PREFIX = f"/session/contrib/{SESSION_ID}" if SESSION_ID else ""
WIZARD_MOUNT = "/astroai-agents"

REWRITE_TYPES = (
    "text/html",
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/x-javascript",
    "application/json",
)

# Static / hub paths only — never rewrite "/api" in JS (CHANNEL_PATTERN).
ABS_PREFIXES = ("/assets/", "/favicon", "/astroai-agents")

AGENTS_CHIP = (
    '<a id="astroai-agents-chip" href="{href}" '
    'style="position:fixed;right:16px;top:16px;z-index:2147483646;'
    "padding:10px 14px;border-radius:8px;background:#3d8bfd;color:#fff;"
    "font:600 14px/1.2 system-ui,sans-serif;text-decoration:none;"
    'border:1px solid #5aa0ff;box-shadow:0 4px 16px rgba(0,0,0,.4)">'
    "AstroAI</a>"
)

RESOURCE_BANNER = (
    '<div id="astroai-resource-banner" '
    'style="position:fixed;left:16px;bottom:16px;z-index:2147483646;'
    "max-width:22rem;padding:10px 12px;border-radius:8px;"
    "background:rgba(20,24,32,.92);color:#e8eaed;"
    "font:500 12px/1.35 system-ui,sans-serif;"
    'border:1px solid #3d4654;box-shadow:0 4px 16px rgba(0,0,0,.35)">'
    "CANFAR Studio: interactive CPU/RAM are capped; "
    '<a href="{href}" style="color:#8ab4ff">AstroAI hub</a> '
    "→ Start batch compute for GPU/heavy jobs. "
    "Scratch is per-pod — persist under /arc."
    "</div>"
)

# Keep channel as "/api"; only the network URL gets the session prefix.
API_SHIM = """<script data-astroai-api-shim>
(function () {
  var P = {prefix};
  if (!P) return;
  function rewrite(u) {
    try {
      var url = new URL(u, location.href);
      if (url.origin === location.origin &&
          url.pathname.indexOf("/api") === 0 &&
          url.pathname.indexOf(P + "/api") !== 0) {
        url.pathname = P + url.pathname;
        return url.href;
      }
    } catch (e) {}
    return u;
  }
  var F = window.fetch;
  window.fetch = function (input, init) {
    if (typeof input === "string") input = rewrite(input);
    else if (typeof Request !== "undefined" && input instanceof Request)
      input = new Request(rewrite(input.url), input);
    return F.call(this, input, init);
  };
  var W = window.WebSocket;
  function Wrapped(url, protocols) {
    return protocols === undefined ? new W(rewrite(url)) : new W(rewrite(url), protocols);
  }
  Wrapped.prototype = W.prototype;
  Wrapped.CONNECTING = W.CONNECTING;
  Wrapped.OPEN = W.OPEN;
  Wrapped.CLOSING = W.CLOSING;
  Wrapped.CLOSED = W.CLOSED;
  window.WebSocket = Wrapped;
})();
</script>"""


def api_shim_html() -> str:
    import json

    return API_SHIM.replace("{prefix}", json.dumps(PREFIX))


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
            text = text.replace(f'"{PREFIX}{abs_prefix}', f'"__KEEP__{abs_prefix}')
            text = text.replace(f"'{PREFIX}{abs_prefix}", f"'__KEEP__{abs_prefix}")
            text = text.replace(f"`{PREFIX}{abs_prefix}", f"`__KEEP__{abs_prefix}")

            text = text.replace(f'"{abs_prefix}', f'"{PREFIX}{abs_prefix}')
            text = text.replace(f"'{abs_prefix}", f"'{PREFIX}{abs_prefix}")
            text = text.replace(f"`{abs_prefix}", f"`{PREFIX}{abs_prefix}")

            text = text.replace(f'"__KEEP__{abs_prefix}', f'"{PREFIX}{abs_prefix}')
            text = text.replace(f"'__KEEP__{abs_prefix}", f"'{PREFIX}{abs_prefix}")
            text = text.replace(f"`__KEEP__{abs_prefix}", f"`{PREFIX}{abs_prefix}")

    if ctype == "text/html":
        text = stick_html_title(text)
        href = f"{PREFIX}{WIZARD_MOUNT}/" if PREFIX else f"{WIZARD_MOUNT}/"
        if PREFIX and "data-astroai-api-shim" not in text:
            shim = api_shim_html()
            lower = text.lower()
            head = lower.find("<head")
            if head >= 0:
                gt = text.find(">", head)
                text = text[: gt + 1] + shim + text[gt + 1 :] if gt >= 0 else shim + text
            else:
                text = shim + text
        if "astroai-agents-chip" not in text:
            chip = AGENTS_CHIP.format(href=href)
            lower = text.lower()
            idx = lower.rfind("</body>")
            text = text[:idx] + chip + text[idx:] if idx >= 0 else text + chip
        if PREFIX and "astroai-resource-banner" not in text:
            banner = RESOURCE_BANNER.format(href=href)
            lower = text.lower()
            idx = lower.rfind("</body>")
            text = text[:idx] + banner + text[idx:] if idx >= 0 else text + banner
    return text.encode("utf-8")


def rewrite_location(value: str) -> str:
    if not PREFIX or not value.startswith("/"):
        return value
    if value.startswith(PREFIX + "/") or value == PREFIX:
        return value
    for abs_prefix in ABS_PREFIXES:
        if value == abs_prefix.rstrip("/") or value.startswith(abs_prefix):
            return PREFIX + value
    if value.startswith(("/api", "/assets", WIZARD_MOUNT)):
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
    # Host is set explicitly from the browser so dsh's trust fence sees the
    # public authority (must match Origin; pair with --trusted-host).
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


def _forward_headers(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    headers = {
        k: v
        for k, v in handler.headers.items()
        if k.lower() not in HOP_BY_HOP and k.lower() != "host"
    }
    # Prefer browser Host so Origin host matches (dsh fence).
    browser_host = handler.headers.get("Host") or handler.headers.get("X-Forwarded-Host")
    if browser_host:
        headers["Host"] = browser_host.split(",", 1)[0].strip()
    return headers


def _forward(
    handler: BaseHTTPRequestHandler, host: str, port: int, path: str, *, rewrite: bool = True
) -> None:
    if is_websocket_request(handler):
        forward_websocket(handler, host, port, path)
        return

    accept = handler.headers.get("Accept", "")
    streaming = "text/event-stream" in accept or path.startswith("/api/events")

    headers = _forward_headers(handler)
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
                b"<p>Use webterm and run <code>astroai agent list --ui</code>.</p>"
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
    if not streaming and rewrite:
        raw = rewrite_body(raw, content_type)

    handler.send_response(upstream.status, upstream.reason)
    for key, value in upstream.getheaders():
        lk = key.lower()
        if lk in HOP_BY_HOP or lk == "host":
            continue
        if lk == "location":
            value = rewrite_location(value)
        if lk == "content-length" and not streaming:
            continue
        handler.send_header(key, value)
    if not streaming:
        handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Connection", "close")
    handler.end_headers()

    if handler.command == "HEAD":
        conn.close()
        return

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


class StudioProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("studio-proxy: %s\n" % (fmt % args))

    def _proxy(self) -> None:
        path = self.path
        if path == WIZARD_MOUNT or path.startswith(WIZARD_MOUNT + "/"):
            rest = path[len(WIZARD_MOUNT) :] or "/"
            _forward(self, WIZARD_HOST, WIZARD_PORT, rest, rewrite=False)
            return
        _forward(self, DSH_HOST, DSH_PORT, path)

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
    server = ThreadingHTTPServer(("0.0.0.0", PUBLIC_PORT), StudioProxyHandler)
    sys.stderr.write(
        f"studio-proxy: listening 0.0.0.0:{PUBLIC_PORT} → {DSH_HOST}:{DSH_PORT} "
        f"wizard={WIZARD_HOST}:{WIZARD_PORT}{WIZARD_MOUNT} "
        f"prefix={PREFIX or '(none)'} api-shim={'on' if PREFIX else 'off'}\n"
    )
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
