"""Reverse proxy for the OpenScience contributed session on CANFAR.

The browser URL is ``/session/contrib/<id>/``; ingress strips that prefix before
the container. OpenScience binds loopback, admits only a loopback Host/Origin and
wants a bearer token on every route, so this proxy:

  * listens on ``0.0.0.0:PUBLIC_PORT`` (default 5000)
  * forwards to ``127.0.0.1:OPENSCIENCE_PORT`` (default 4796) as a loopback client
    with the session token; CANFAR cookies and forwarding headers stay behind
  * serves ``/__astroai/boot.js`` (base path for the patched workspace; the CSP
    forbids inline scripts) and ``/__astroai/health`` (container healthcheck)
  * routes ``/astroai-agents/*`` to the AstroAI hub (model keys, CANFAR compute)
  * streams server-sent events unbuffered and splices the terminal websocket
  * shows a starting page while startup-openscience.sh (re)starts the server
"""

from __future__ import annotations

import contextlib
import html
import json
import os
import select
import socket
import sys
from collections.abc import Callable, Iterable
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

PUBLIC_PORT = int(os.environ.get("ASTROAI_PUBLIC_PORT", "5000"))
OPENSCIENCE_HOST = os.environ.get("ASTROAI_OPENSCIENCE_HOST", "127.0.0.1")
OPENSCIENCE_PORT = int(os.environ.get("ASTROAI_OPENSCIENCE_PORT", "4796"))
WIZARD_HOST = os.environ.get("ASTROAI_AGENT_WIZARD_HOST", "127.0.0.1")
WIZARD_PORT = int(os.environ.get("ASTROAI_AGENT_WIZARD_PORT", "4792"))
SESSION_ID = (os.environ.get("skaha_sessionid") or "").strip()  # noqa: SIM112 — platform env var is lowercase
PREFIX = f"/session/contrib/{SESSION_ID}" if SESSION_ID else ""
WIZARD_MOUNT = "/astroai-agents"
BOOT_PATH = "/__astroai/boot.js"
HEALTH_PATH = "/__astroai/health"
RETRY_PARAM = "astroai-retry"

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
_DROP_FOR_OPENSCIENCE = ("host", "authorization", "cookie", "referer", "x-real-ip", "forwarded")

# Hub keys that each unlock models in OpenScience (see the canfar-lab registry entry).
MODEL_KEYS = (
    "OPENROUTER_API_KEY",
    "OPENCODE_API_KEY",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
)
SCRATCH_HISTORY_FLAG = "history-on-scratch"

_CHIP_BASE = (
    "position:fixed;z-index:2147483646;right:14px;bottom:14px;padding:6px 11px;"
    "border-radius:999px;font:600 12px/1.2 system-ui,sans-serif;text-decoration:none;"
)
# rel=external: the workspace router otherwise claims same-origin links under its base.
HUB_CHIP = (
    '<a id="astroai-agents-chip" href="{href}" rel="external" '
    'title="Model keys and CANFAR compute" style="'
    + _CHIP_BASE
    + 'background:rgba(30,32,48,.92);border:1px solid #494d64;color:#cad3f5">AstroAI</a>'
)
KEY_CHIP = (
    '<a id="astroai-agents-chip" href="{href}" rel="external" data-needs-key '
    'title="OpenScience needs a model key before it can answer. Add one in the AstroAI hub '
    '(OpenRouter is the simplest)." style="'
    + _CHIP_BASE
    + 'background:#3d8bfd;border:1px solid #8aadf4;color:#fff">Add a model key</a>'
)
SCRATCH_BANNER = (
    '<div data-astroai-banner role="status" style="position:fixed;z-index:2147483646;left:14px;'
    "bottom:14px;max-width:26rem;padding:10px 12px;border-radius:8px;background:#363a4f;"
    "border:1px solid #eed49f;color:#cad3f5;font:13px/1.4 system-ui,sans-serif\">"
    "<b>History on scratch.</b> Your saved OpenScience history is in use by session "
    "{holder}, so this session keeps its chats on scratch, which is deleted when the session "
    "ends. Files you save under /arc are safe. To use your saved history, close the other "
    "session and restart this one. "
    '<button type="button" data-astroai-dismiss style="margin-left:4px;background:none;'
    'border:0;color:#8aadf4;cursor:pointer;font:inherit">Dismiss</button></div>'
)


def state_file(name: str) -> Path | None:
    state = os.environ.get("ASTROAI_OPENSCIENCE_STATE", "").strip().rstrip("/")
    return Path(state) / name if state else None


def read_token() -> str | None:
    path = state_file("openscience-token")
    try:
        return (path.read_text(encoding="utf-8").strip() or None) if path else None
    except OSError:
        return None


def gave_up() -> bool:
    failed = state_file("openscience.failed")
    return bool(failed and failed.exists())


def retry_start() -> None:
    """Clear the give-up flag; the startup supervisor starts the server again."""
    failed = state_file("openscience.failed")
    if failed:
        failed.unlink(missing_ok=True)


def has_model_key() -> bool:
    """Whether OpenScience can reach any model: a hub key (environment or the shared
    dotenv) or a provider saved in OpenScience's own settings. Names only; values
    are never read beyond "non-empty"."""
    if any(os.environ.get(name, "").strip() for name in MODEL_KEYS):
        return True
    home = Path.home()
    with contextlib.suppress(OSError, UnicodeDecodeError):
        for line in (home / ".astroai" / "lab" / ".env").read_text(encoding="utf-8").splitlines():
            name, sep, value = line.strip().removeprefix("export ").partition("=")
            if sep and name.strip() in MODEL_KEYS and value.strip().strip("'\""):
                return True
    scratch = Path(os.environ.get("SCRATCH") or "/scratch")
    for data in (home / ".openscience", scratch / ".openscience"):
        with contextlib.suppress(OSError, ValueError):
            if json.loads((data / "auth.json").read_text(encoding="utf-8")):
                return True
    return False


def scratch_history_holder() -> str | None:
    flag = state_file(SCRATCH_HISTORY_FLAG)
    try:
        return (flag.read_text(encoding="utf-8").strip() or "another session") if flag else None
    except OSError:
        return None


_DISMISS_JS = (
    "document.addEventListener('click',function(e){"
    "var b=e.target.closest&&e.target.closest('[data-astroai-dismiss]');"
    "if(b){b.closest('[data-astroai-banner]').remove();}});\n"
)


def boot_js() -> bytes:
    # The workspace CSP forbids inline handlers, so the banner's button is wired here.
    return (
        f"window.__OPENSCIENCE_BASE_URL__={json.dumps(PREFIX)};"
        "window.__OPENSCIENCE_TRUSTED_PROXY__=true;\n" + _DISMISS_JS
    ).encode()


def inject_html(data: bytes, content_type: str) -> bytes:
    """Absolute asset URLs (index.html is also served for deep links, where ``./``
    would resolve under the route), the boot script ahead of the deferred module
    bundle, the hub chip (a call to add a key while OpenScience has no model) and a
    notice when this session's history lives on scratch."""
    if content_type.split(";", 1)[0].strip().lower() != "text/html":
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    text = text.replace('="./', f'="{PREFIX}/')
    boot = f'<script data-astroai-openscience src="{PREFIX}{BOOT_PATH}"></script>'
    head = text.lower().find("<head")
    gt = text.find(">", head) if head >= 0 else -1
    text = text[: gt + 1] + boot + text[gt + 1 :] if gt >= 0 else boot + text
    extra = ""
    if "astroai-agents-chip" not in text:
        chip = HUB_CHIP if has_model_key() else KEY_CHIP
        extra += chip.format(href=f"{PREFIX}{WIZARD_MOUNT}/")
    holder = scratch_history_holder()
    if holder and "data-astroai-banner" not in text:
        extra += SCRATCH_BANNER.format(holder=html.escape(holder))
    if extra:
        idx = text.lower().rfind("</body>")
        text = text[:idx] + extra + text[idx:] if idx >= 0 else text + extra
    return text.encode("utf-8")


def rewrite_location(value: str) -> str:
    if not PREFIX or not value.startswith("/") or value.startswith("//"):
        return value
    if value == PREFIX or value.startswith(PREFIX + "/"):
        return value
    return PREFIX + value


def openscience_headers(
    items: Iterable[tuple[str, str]], token: str | None, *, websocket: bool = False
) -> list[tuple[str, str]]:
    """Requests as the loopback client OpenScience expects."""
    out: list[tuple[str, str]] = []
    for key, value in items:
        lk = key.lower()
        if lk in _DROP_FOR_OPENSCIENCE or lk.startswith("x-forwarded-"):
            continue
        if not websocket and (lk in HOP_BY_HOP or lk == "accept-encoding"):
            continue
        if lk == "origin":
            value = f"http://{OPENSCIENCE_HOST}:{OPENSCIENCE_PORT}"
        out.append((key, value))
    out.append(("Host", f"{OPENSCIENCE_HOST}:{OPENSCIENCE_PORT}"))
    if not websocket:
        out.append(("Accept-Encoding", "identity"))
    if token:
        out.append(("Authorization", f"Bearer {token}"))
    return out


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _page(title: str, body: str, *, refresh: str | None = None) -> bytes:
    meta = f'<meta http-equiv="refresh" content="2;url={html.escape(refresh)}"/>' if refresh else ""
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"/>'
        f"{meta}<title>OpenScience</title></head>"
        "<body style='font-family:system-ui,-apple-system,sans-serif;padding:2rem;"
        "line-height:1.5;background:#181926;color:#cad3f5;max-width:44rem'>"
        f"<h1>{title}</h1>{body}"
        f"<p><a href='{PREFIX}{WIZARD_MOUNT}/' style='color:#8aadf4'>Model keys and compute</a>"
        "</p></body></html>"
    ).encode()


def starting_html(target: str) -> bytes:
    return _page(
        "OpenScience is starting",
        "<p>This takes 10–30 seconds (and happens again after model keys are saved); "
        "the page reloads by itself.</p>",
        refresh=target,
    )


def failed_html(target: str) -> bytes:
    state = os.environ.get("ASTROAI_OPENSCIENCE_STATE", "$SCRATCH/.openscience-session")
    retry = target + ("&" if "?" in target else "?") + f"{RETRY_PARAM}=1"
    return _page(
        "OpenScience could not start",
        "<p>It exited several times in a row. The log is at</p>"
        "<pre style='background:#24273a;padding:.75rem;border-radius:6px'>"
        f"{html.escape(state)}/openscience.log</pre>"
        f"<p><a href='{html.escape(retry)}' style='color:#8aadf4'>Try again</a></p>",
    )


WIZARD_DOWN_HTML = (
    b"<!DOCTYPE html><html><body style='font-family:sans-serif;padding:2rem'>"
    b"<h1>Hub unavailable</h1>"
    b"<p>Model keys can also be set in OpenScience settings.</p></body></html>"
)


def _send(handler: BaseHTTPRequestHandler, status: int, body: bytes, ctype: str) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "close")
    handler.end_headers()
    if handler.command != "HEAD":
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            handler.wfile.write(body)


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    _send(handler, status, json.dumps(payload).encode(), "application/json")


def _redirect(handler: BaseHTTPRequestHandler, location: str) -> None:
    handler.send_response(302)
    handler.send_header("Location", location)
    handler.send_header("Content-Length", "0")
    handler.send_header("Connection", "close")
    handler.end_headers()


def is_websocket_request(handler: BaseHTTPRequestHandler) -> bool:
    conn = handler.headers.get("Connection", "").lower()
    upgrade = handler.headers.get("Upgrade", "").lower()
    return "upgrade" in conn and "websocket" in upgrade


def _splice_sockets(client: socket.socket, upstream: socket.socket) -> None:
    sockets = [client, upstream]
    try:
        while True:
            readable, _, _ = select.select(sockets, [], [], 300)
            for src in readable:
                dst = upstream if src is client else client
                data = src.recv(65536)
                if not data:
                    return
                dst.sendall(data)
    except OSError:
        return


def forward_websocket(
    handler: BaseHTTPRequestHandler,
    host: str,
    port: int,
    path: str,
    headers: Iterable[tuple[str, str]],
) -> None:
    try:
        upstream = socket.create_connection((host, port), timeout=30)
    except OSError as exc:
        handler.send_error(502, f"upstream unreachable: {exc}")
        return
    lines = [f"{handler.command} {path} HTTP/1.1", *(f"{k}: {v}" for k, v in headers)]
    try:
        upstream.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1"))
        _splice_sockets(handler.connection, upstream)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            upstream.close()


def forward(
    handler: BaseHTTPRequestHandler,
    host: str,
    port: int,
    path: str,
    headers: list[tuple[str, str]],
    *,
    body_filter: Callable[[bytes, str], bytes] | None = None,
    location: Callable[[str], str] = rewrite_location,
) -> bool:
    """Forward one request; False when the upstream is unreachable."""
    length = int(handler.headers.get("Content-Length", "0") or "0")
    body = handler.rfile.read(length) if length > 0 else None
    conn = HTTPConnection(host, port, timeout=600)
    try:
        conn.request(handler.command, path, body=body, headers=dict(headers))
        upstream = conn.getresponse()
    except OSError:
        conn.close()
        return False

    content_type = upstream.getheader("Content-Type") or ""
    streaming = content_type.split(";", 1)[0].strip().lower() == "text/event-stream"
    raw = b"" if streaming else upstream.read()
    filtered = body_filter(raw, content_type) if body_filter and not streaming else raw
    rewritten = filtered != raw

    handler.send_response(upstream.status, upstream.reason)
    for key, value in upstream.getheaders():
        lk = key.lower()
        if lk in HOP_BY_HOP:
            continue
        if lk == "location":
            value = location(value)
        if rewritten and lk in ("cache-control", "etag", "last-modified", "expires"):
            continue
        handler.send_header(key, value)
    if streaming:
        handler.send_header("X-Accel-Buffering", "no")
    else:
        handler.send_header("Content-Length", str(len(filtered)))
    if rewritten:
        handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "close")
    handler.end_headers()

    try:
        if streaming:
            while True:
                # read1: return what has arrived; read(n) waits for n bytes and stalls events.
                chunk = upstream.read1(8192)
                if not chunk:
                    break
                handler.wfile.write(chunk)
                handler.wfile.flush()
        elif handler.command != "HEAD":
            handler.wfile.write(filtered)
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        conn.close()
    return True


def _passthrough_headers(items: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(k, v) for k, v in items if k.lower() not in HOP_BY_HOP] + [
        ("Accept-Encoding", "identity")
    ]


class OpenScienceProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("openscience-proxy: %s\n" % (fmt % args))

    def _proxy(self) -> None:
        parts = urlsplit(self.path)
        route = parts.path or "/"
        if PREFIX and (route == PREFIX or route.startswith(PREFIX + "/")):
            route = route[len(PREFIX) :] or "/"
        params = parse_qsl(parts.query, keep_blank_values=True)
        kept = [(k, v) for k, v in params if k != RETRY_PARAM]
        query = urlencode(kept) if len(kept) != len(params) else parts.query
        target = route + (f"?{query}" if query else "")

        if route == BOOT_PATH and self.command in ("GET", "HEAD"):
            _send(self, 200, boot_js(), "text/javascript; charset=utf-8")
            return
        if route == HEALTH_PATH:
            up = port_open(OPENSCIENCE_HOST, OPENSCIENCE_PORT)
            _send_json(self, 200, {"proxy": "ok", "openscience": up, "failed": gave_up()})
            return
        if route == WIZARD_MOUNT:
            _redirect(self, f"{PREFIX}{WIZARD_MOUNT}/" + (f"?{query}" if query else ""))
            return
        if route.startswith(WIZARD_MOUNT + "/"):
            rest = target[len(WIZARD_MOUNT) :]
            if not forward(
                self, WIZARD_HOST, WIZARD_PORT, rest, _passthrough_headers(self.headers.items())
            ):
                _send(self, 503, WIZARD_DOWN_HTML, "text/html; charset=utf-8")
            return

        if len(kept) != len(params):
            retry_start()
        token = read_token()
        if is_websocket_request(self):
            headers = openscience_headers(self.headers.items(), token, websocket=True)
            forward_websocket(self, OPENSCIENCE_HOST, OPENSCIENCE_PORT, target, headers)
            return
        headers = openscience_headers(self.headers.items(), token)
        if forward(
            self, OPENSCIENCE_HOST, OPENSCIENCE_PORT, target, headers, body_filter=inject_html
        ):
            return
        failed = gave_up()
        if self.command == "GET" and "text/html" in self.headers.get("Accept", ""):
            page = failed_html(PREFIX + target) if failed else starting_html(PREFIX + target)
            _send(self, 503 if failed else 200, page, "text/html; charset=utf-8")
        else:
            _send_json(self, 503, {"error": "failed" if failed else "starting"})

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _proxy  # noqa: N815


def main() -> int:
    server = ThreadingHTTPServer(("0.0.0.0", PUBLIC_PORT), OpenScienceProxyHandler)
    sys.stderr.write(
        f"openscience-proxy: listening 0.0.0.0:{PUBLIC_PORT} → "
        f"{OPENSCIENCE_HOST}:{OPENSCIENCE_PORT} hub={WIZARD_HOST}:{WIZARD_PORT}{WIZARD_MOUNT} "
        f"prefix={PREFIX or '(none)'}\n"
    )
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
