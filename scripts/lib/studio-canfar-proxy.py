"""Reverse-proxy dsh web for CANFAR contributed Studio sessions.

dsh builds every RPC/WebSocket URL as ``new URL('/api/…', location.origin)``,
so absolute ``/api`` escapes ``/session/contrib/<id>/``. Rewriting the string
``\"/api\"`` in bundles to a multi-segment path also breaks dsh's
``CHANNEL_PATTERN`` (single-segment channels only).

This proxy:
  * listens on ``0.0.0.0:PUBLIC_PORT`` (default 5000)
  * forwards to ``127.0.0.1:DSH_PORT`` (default 3080)
  * routes ``/astroai-agents/*`` to the AstroAI hub sidecar
  * routes ``/astroai-terminal/*`` to ghostty-web (WebSocket splice)
  * splices WebSocket upgrades (dsh ``/api/remote.mux`` + terminal ``/ws``)
  * injects an early fetch/WebSocket shim that prefixes ``/api`` with the
    session path (channel string stays ``/api`` for client validation)
  * injects Terminal + AstroAI chips (proxy-only; no dsh fork)
  * rewrites ``/assets`` / favicon / hub / terminal links the same way as orx
  * forwards browser ``Host`` + ``Origin`` so dsh's trust fence can match
    (start dsh with ``--trusted-host <public-host>``)
"""

from __future__ import annotations

import contextlib
import os
import re
import select
import socket
import sys
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from session_title import stick_html_title  # noqa: E402

PUBLIC_PORT = int(os.environ.get("ASTROAI_STUDIO_PORT", "5000"))
DSH_HOST = os.environ.get("DSH_HOST", "127.0.0.1")
DSH_PORT = int(os.environ.get("DSH_PORT", "3080"))
WIZARD_HOST = os.environ.get("ASTROAI_AGENT_WIZARD_HOST", "127.0.0.1")
WIZARD_PORT = int(os.environ.get("ASTROAI_AGENT_WIZARD_PORT", "4792"))
TERMINAL_HOST = os.environ.get("ASTROAI_TERMINAL_HOST", "127.0.0.1")
TERMINAL_PORT = int(os.environ.get("ASTROAI_TERMINAL_PORT", "4793"))
SESSION_ID = (os.environ.get("skaha_sessionid") or "").strip()  # noqa: SIM112
PREFIX = f"/session/contrib/{SESSION_ID}" if SESSION_ID else ""
WIZARD_MOUNT = "/astroai-agents"
TERMINAL_MOUNT = "/astroai-terminal"
COOKIE_PREFIX = "dsh-auth-"
BRAND_TITLE = "AstroAI Studio"


def _token_file_path() -> str:
    explicit = os.environ.get("ASTROAI_DSH_TOKEN_FILE", "").strip()
    if explicit:
        return explicit
    state = os.environ.get("ASTROAI_STUDIO_STATE", "").strip().rstrip("/")
    return f"{state}/dsh-web-token" if state else ""


TOKEN_FILE = _token_file_path()

REWRITE_TYPES = (
    "text/html",
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/x-javascript",
    "application/json",
)

# Static / hub paths — rewrite "/api/…" (trailing slash) for img/present/upload
# strings in bundles. Never rewrite the bare channel "/api" (CHANNEL_PATTERN).
ABS_PREFIXES = (
    "/api/",
    "/assets/",
    "/favicon",
    "/plugins/",
    "/astroai-agents",
    "/astroai-terminal",
)

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
# Keep channel as "/api"; only network URLs get the session prefix.
# Also wrap EventSource (HMR /plugins/events) and cover /api/file img src via
# ABS_PREFIXES rewrite of quoted "/api/" in bundles.
API_SHIM = """<script data-astroai-api-shim>
(function () {
  var P = {prefix};
  if (!P) return;
  // dsh Settings (Models/providers) only persist when connection.isLoopback.
  // Page host is workloads.canfar.net, not 127.0.0.1 — mark ownsHost so the
  // session is treated as the operator's Host (paired with --trusted-host).
  var T = globalThis.__DSH_TRANSPORT__;
  if (!T) T = globalThis.__DSH_TRANSPORT__ = {};
  T.ownsHost = true;
  function needs(path) {
    return (path === "/api" || path.indexOf("/api/") === 0 ||
            path.indexOf("/plugins/") === 0) &&
           path.indexOf(P + "/") !== 0;
  }
  function rewrite(u) {
    try {
      var url = new URL(u, location.href);
      if (url.origin === location.origin && needs(url.pathname)) {
        url.pathname = P + url.pathname;
        return url.href;
      }
    } catch (e) {}
    return u;
  }
  var F = window.fetch;
  window.fetch = function (input, init) {
    // dsh RPC uses fetch(new URL('/api/...', origin)) — must rewrite URL objects.
    if (typeof input === "string") input = rewrite(input);
    else if (typeof URL !== "undefined" && input instanceof URL)
      input = new URL(rewrite(input.href));
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
  if (typeof window.EventSource === "function") {
    var E = window.EventSource;
    function ES(url, config) {
      return config === undefined ? new E(rewrite(url)) : new E(rewrite(url), config);
    }
    ES.prototype = E.prototype;
    ES.CONNECTING = E.CONNECTING;
    ES.OPEN = E.OPEN;
    ES.CLOSED = E.CLOSED;
    window.EventSource = ES;
  }
})();
</script>"""


def api_shim_html() -> str:
    import json

    return API_SHIM.replace("{prefix}", json.dumps(PREFIX))


def read_launch_token() -> str | None:
    """Process launch token captured by startup-studio.sh from dsh's boot URL."""
    path = TOKEN_FILE or _token_file_path()
    if not path:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def has_dsh_auth_cookie(cookie_header: str | None) -> bool:
    if not cookie_header:
        return False
    return any(part.strip().startswith(COOKIE_PREFIX) for part in cookie_header.split(";"))


def expire_dsh_auth_cookies(cookie_header: str | None) -> list[str]:
    """Expire every ``dsh-auth-*`` cookie the browser sent (stale session recovery)."""
    if not cookie_header:
        return []
    headers: list[str] = []
    seen: set[str] = set()
    for part in cookie_header.split(";"):
        name = part.strip().split("=", 1)[0].strip()
        if not name.startswith(COOKIE_PREFIX) or name in seen:
            continue
        seen.add(name)
        # Clear both Path=/ (dsh default) and PREFIX/ in case a prior rewrite
        # scoped the cookie to the session path.
        headers.append(f"{name}=; Max-Age=0; Path=/")
        if PREFIX:
            headers.append(f"{name}=; Max-Age=0; Path={PREFIX}/")
    return headers


def rewrite_set_cookie(value: str) -> str:
    """Make dsh auth cookies usable after Skaha portal → workloads Connect.

    dsh emits ``SameSite=Strict``. Connect is a cross-site top-level navigation
    from the science portal host, so Strict cookies set on the ``?token=``
    hop are omitted on the following ``/`` redirect and the SPA 401s with
    "dsh web authentication required".
    """
    return re.sub(r"(?i)SameSite=Strict", "SameSite=Lax", value)


def index_token_redirect(path: str, cookie_header: str | None) -> str | None:
    """If Skaha hit ``/`` without ``?token=``, redirect to the dsh launch token URL.

    Skaha Connect URLs never include dsh's one-shot ``?token=``; without it the
    SPA answers 401. Prefer a PREFIX-qualified Location so a leading ``/`` does
    not bounce the browser off ``/session/contrib/<id>/`` onto the site root.
    """
    parsed = urlparse(path)
    route = parsed.path or "/"
    if route not in ("/", "") and route != f"{PREFIX}/" and route != PREFIX:
        return None
    if parse_qs(parsed.query).get("token"):
        return None
    if has_dsh_auth_cookie(cookie_header):
        return None
    token = read_launch_token()
    if not token:
        return None
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["token"] = [token]
    target_path = f"{PREFIX}/" if PREFIX else "/"
    return urlunparse(("", "", target_path, "", urlencode(query, doseq=True), ""))


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
        if PREFIX:
            # dsh emits <base href="/"> which makes ./assets resolve at the
            # workloads site root (404 → blank SPA). Point base at the session.
            for quote in ('"', "'"):
                text = text.replace(
                    f"<base href={quote}/{quote}>",
                    f"<base href={quote}{PREFIX}/{quote}>",
                )
                text = text.replace(
                    f"<base href={quote}/{quote} />",
                    f"<base href={quote}{PREFIX}/{quote} />",
                )
        text = stick_html_title(text, BRAND_TITLE)
        if PREFIX and "data-astroai-api-shim" not in text:
            shim = api_shim_html()
            lower = text.lower()
            head = lower.find("<head")
            if head >= 0:
                gt = text.find(">", head)
                text = text[: gt + 1] + shim + text[gt + 1 :] if gt >= 0 else shim + text
            else:
                text = shim + text
        # Fingerprint which proxy build is serving (Skaha image-cache checks).
        if 'data-astroai-proxy-rev="' not in text:
            text = text.replace(
                "<head>",
                '<head><meta data-astroai-proxy-rev="9" />',
                1,
            )
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
    """Keep absolute Locations under the Skaha session path.

    dsh's post-auth ``303 Location: /`` must become ``PREFIX/`` — otherwise the
    browser leaves ``/session/contrib/<id>/`` for the workloads site root
    (blank page, no SPA error).
    """
    if not PREFIX or not value.startswith("/"):
        return value
    if value == PREFIX or value.startswith(PREFIX + "/") or value.startswith(PREFIX + "?"):
        return value
    return PREFIX + value


def upstream_path(path: str) -> str:
    """Strip Skaha ``/session/contrib/<id>`` before forwarding to loopback dsh.

    Some ingresses pass the public path through unchanged. dsh only serves
    ``/``, ``/api``, ``/assets``, … — a prefixed ``/?token=`` is 404 and the
    SPA never authenticates (blank page).
    """
    if not PREFIX:
        return path
    parsed = urlparse(path)
    route = parsed.path or "/"
    if route == PREFIX or route.startswith(PREFIX + "/"):
        rest = route[len(PREFIX) :] or "/"
        return urlunparse(("", "", rest, "", parsed.query, parsed.fragment))
    return path


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
        if k.lower() not in HOP_BY_HOP and k.lower() not in ("host", "accept-encoding")
    }
    # Prefer browser Host so Origin host matches (dsh fence).
    browser_host = handler.headers.get("Host") or handler.headers.get("X-Forwarded-Host")
    if browser_host:
        headers["Host"] = browser_host.split(",", 1)[0].strip()
    # Must rewrite uncompressed HTML/JS. Browser Accept-Encoding: gzip makes
    # dsh return opaque bytes → rewrite_body no-ops → <base href="/"> left
    # intact → blank SPA under /session/contrib/<id>/.
    headers["Accept-Encoding"] = "identity"
    return headers


STARTING_HTML = (
    b"<!DOCTYPE html><html><head>"
    b'<meta charset="utf-8"/>'
    b'<meta http-equiv="refresh" content="3"/>'
    b"<title>AstroAI Studio</title></head>"
    b"<body style='font-family:system-ui,sans-serif;padding:2rem;line-height:1.5'>"
    b"<h1>AstroAI Studio is starting</h1>"
    b"<p>Preparing the coding UI - this page refreshes automatically.</p>"
    b"</body></html>"
)


def _is_index_path(path: str) -> bool:
    """Bare ``/`` or the Skaha-prefixed session index (with optional query)."""
    route = urlparse(path).path or "/"
    if route in ("/", ""):
        return True
    if PREFIX and (route == PREFIX or route == f"{PREFIX}/"):
        return True
    return False


def _send_html(handler: BaseHTTPRequestHandler, status: int, body: bytes) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if handler.command != "HEAD":
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            handler.wfile.write(body)


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
                b"<h1>AstroAI unavailable</h1>"
                b"<p>Use Terminal and run <code>astroai agent list --ui</code>.</p>"
                b"</body></html>"
            )
            _send_html(handler, 503, fallback)
            return
        if host == TERMINAL_HOST and port == TERMINAL_PORT:
            fallback = (
                b"<!DOCTYPE html><html><body style='font-family:sans-serif;padding:2rem'>"
                b"<h1>Terminal unavailable</h1>"
                b"<p>ghostty-web is not running in this Studio session.</p>"
                b"</body></html>"
            )
            _send_html(handler, 503, fallback)
            return
        # Boot race: Skaha Connect hits :5000 before dsh listens. Return 200 so
        # ingress/`Bad Gateway` is not the Connect experience (marimo pattern).
        if host == DSH_HOST and port == DSH_PORT and _is_index_path(path):
            _send_html(handler, 200, STARTING_HTML)
            handler.log_message('"starting" %s (dsh not ready: %s)', path, exc)
            return
        handler.send_error(502, f"upstream unreachable: {exc}")
        return

    content_type = upstream.getheader("Content-Type") or ""
    raw = b"" if streaming else upstream.read()

    # Stale dsh-auth-* cookie: proxy skipped ?token= redirect, dsh answers 401
    # "authentication required". Clear cookies and bounce to the launch token.
    if (
        not streaming
        and upstream.status == 401
        and host == DSH_HOST
        and port == DSH_PORT
        and _is_index_path(path)
        and not parse_qs(urlparse(path).query).get("token")
    ):
        loc = index_token_redirect("/", None)
        if loc:
            handler.send_response(302, "Found")
            handler.send_header("Location", loc)
            handler.send_header("Cache-Control", "no-store")
            for cookie in expire_dsh_auth_cookies(handler.headers.get("Cookie")):
                handler.send_header("Set-Cookie", cookie)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            handler.log_message('"auth-recovery" %s → %s', path, loc)
            conn.close()
            return

    if not streaming and rewrite:
        raw = rewrite_body(raw, content_type)

    handler.send_response(upstream.status, upstream.reason)
    for key, value in upstream.getheaders():
        lk = key.lower()
        if lk in HOP_BY_HOP or lk == "host":
            continue
        if lk == "location":
            value = rewrite_location(value)
        if lk == "set-cookie":
            value = rewrite_set_cookie(value)
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
        if self.command in ("GET", "HEAD"):
            loc = index_token_redirect(self.path, self.headers.get("Cookie"))
            if loc is not None:
                self.send_response(302, "Found")
                self.send_header("Location", loc)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                self.log_message('"token-redirect" %s → %s', self.path, loc)
                return
            # No cookie and no launch token yet: keep Connect on the starting
            # page (auto-refresh) instead of proxying a bare dsh 401/404.
            if _is_index_path(self.path) and not has_dsh_auth_cookie(
                self.headers.get("Cookie")
            ):
                if not read_launch_token():
                    _send_html(self, 200, STARTING_HTML)
                    self.log_message('"starting" %s (waiting for dsh token)', self.path)
                    return
        # Mounts are under the Skaha prefix; strip before matching sidecars.
        public = upstream_path(self.path)
        route = urlparse(public).path or "/"
        if route == WIZARD_MOUNT or route.startswith(WIZARD_MOUNT + "/"):
            rest = route[len(WIZARD_MOUNT) :] or "/"
            qs = urlparse(public).query
            if qs:
                rest = f"{rest}?{qs}" if "?" not in rest else f"{rest}&{qs}"
            _forward(self, WIZARD_HOST, WIZARD_PORT, rest, rewrite=False)
            return
        if route == TERMINAL_MOUNT or route.startswith(TERMINAL_MOUNT + "/"):
            rest = route[len(TERMINAL_MOUNT) :] or "/"
            qs = urlparse(public).query
            if qs:
                rest = f"{rest}?{qs}" if "?" not in rest else f"{rest}&{qs}"
            _forward(self, TERMINAL_HOST, TERMINAL_PORT, rest, rewrite=False)
            return
        _forward(self, DSH_HOST, DSH_PORT, public)

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
        f"terminal={TERMINAL_HOST}:{TERMINAL_PORT}{TERMINAL_MOUNT} "
        f"prefix={PREFIX or '(none)'} api-shim={'on' if PREFIX else 'off'}\n"
    )
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
