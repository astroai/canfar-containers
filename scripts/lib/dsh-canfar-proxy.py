"""Public :5000 proxy for astroai/dsh sessions on CANFAR.

Routes come from a tab table (``/opt/astroai/studio/tabs.json``, override with
``ASTROAI_TABS_FILE``). Each tab is ``{id, label, prefix, port}`` plus optional
keys; the ``/`` tab is dsh, everything else is matched by path prefix:

* ``strip``      (default true)  drop the tab prefix before forwarding and send
                                 ``X-Forwarded-Prefix``. Set false for tools that
                                 serve under their own base path (Jupyter, VS Code).
* ``bearer_env`` env var whose value is sent as ``Authorization: Bearer`` (Lab).
* ``badge``      path under the tab polled by the tab strip; a JSON
                 ``{"pending": n, "ready": bool, "kind": ...}`` puts a count on the tab.
* ``port_env``   env var that overrides ``port``.

Adding a tool later is one Dockerfile layer plus one line in tabs.json.

The proxy also: binds :5000 before the session is ready (Skaha liveness),
sends Connect to dsh's launch-token URL, rewrites dsh's absolute URLs under
``/session/contrib/<id>`` with a fetch/WebSocket/EventSource shim, splices
WebSockets, streams Server-Sent Events, injects the tab strip into HTML, and
answers ``/api/studio/status`` for the image healthcheck. Stdlib only.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import select
import socket
import sys
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from session_title import stick_html_title  # noqa: E402

PUBLIC_PORT = int(os.environ.get("ASTROAI_STUDIO_PORT", "5000"))
UPSTREAM_HOST = "127.0.0.1"
SESSION_ID = (os.environ.get("skaha_sessionid") or "").strip()  # noqa: SIM112
PREFIX = f"/session/contrib/{SESSION_ID}" if SESSION_ID else ""
TABS_FILE = os.environ.get("ASTROAI_TABS_FILE", "/opt/astroai/studio/tabs.json")
COOKIE_PREFIX = "dsh-auth-"
BRAND_TITLE = os.environ.get("ASTROAI_TAB_TITLE", "AstroAI")
PROXY_REVISION = "dsh-1"
ASSET_ROUTE = "/__dsh/tabs.js"
STATUS_ROUTE = "/api/studio/status"

DEFAULT_TABS: list[dict[str, Any]] = [
    {"id": "agent", "label": "Agent", "prefix": "/", "port": 3080, "port_env": "DSH_PORT"},
    {
        "id": "lab",
        "label": "Lab",
        "prefix": "/lab",
        "port": 4792,
        "port_env": "CANFAR_LAB_WEB_PORT",
        "bearer_env": "CANFAR_LAB_WEB_TOKEN",
        "badge": "api/onboarding",
    },
]


# ---------------------------------------------------------------------------
# Tab table
# ---------------------------------------------------------------------------


def _clean_tab(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    prefix = str(raw.get("prefix") or "").rstrip("/") or "/"
    if not re.fullmatch(r"/[A-Za-z0-9_.-]*", prefix):
        return None
    try:
        port = int(raw.get("port_env") and os.environ.get(raw["port_env"]) or raw["port"])
    except (KeyError, TypeError, ValueError):
        return None
    tab_id = str(raw.get("id") or prefix.strip("/") or "root")
    return {
        "id": tab_id,
        "label": str(raw.get("label") or tab_id),
        "prefix": prefix,
        "port": port,
        "strip": bool(raw.get("strip", True)),
        "bearer_env": str(raw.get("bearer_env") or ""),
        "badge": str(raw.get("badge") or "").lstrip("/"),
    }


def load_tabs(path: str = TABS_FILE) -> list[dict[str, Any]]:
    """Tabs in display order. Falls back to Agent + Lab when the file is unusable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = data.get("tabs") if isinstance(data, dict) else data
    except (OSError, ValueError):
        rows = None
    tabs = [t for t in (_clean_tab(r) for r in rows or []) if t]
    if not any(t["prefix"] == "/" for t in tabs):
        tabs = [t for t in (_clean_tab(r) for r in DEFAULT_TABS) if t]
    return tabs


TABS = load_tabs()


def match_tab(route: str, tabs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Longest prefix wins; the ``/`` tab (dsh) catches everything else."""
    tabs = TABS if tabs is None else tabs
    best = next(t for t in tabs if t["prefix"] == "/")
    for tab in tabs:
        p = tab["prefix"]
        if p != "/" and (route == p or route.startswith(p + "/")) and len(p) > len(best["prefix"]):
            best = tab
    return best


def tab_href(tab: dict[str, Any]) -> str:
    return f"{PREFIX}/" if tab["prefix"] == "/" else f"{PREFIX}{tab['prefix']}/"


# ---------------------------------------------------------------------------
# dsh launch token (Connect)
# ---------------------------------------------------------------------------


def read_launch_token() -> str | None:
    """dsh's launch token, captured from its boot log by startup-dsh.sh."""
    path = os.environ.get("ASTROAI_DSH_TOKEN_FILE", "").strip()
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def has_dsh_auth_cookie(cookie_header: str | None) -> bool:
    return any(p.strip().startswith(COOKIE_PREFIX) for p in (cookie_header or "").split(";"))


def expire_dsh_auth_cookies(cookie_header: str | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for part in (cookie_header or "").split(";"):
        name = part.strip().split("=", 1)[0].strip()
        if name.startswith(COOKIE_PREFIX) and name not in seen:
            seen.add(name)
            out.append(f"{name}=; Max-Age=0; Path=/")
            if PREFIX:
                out.append(f"{name}=; Max-Age=0; Path={PREFIX}/")
    return out


ONBOARDED_COOKIE = "astroai-onboarded"


def onboarding_redirect(handler: BaseHTTPRequestHandler) -> tuple[str | None, bool]:
    """First Agent page a browser loads in a brand-new session: go to the checklist.

    Returns (location, remember). Asks the badge tab (the Lab) once; *remember*
    means set a cookie so later loads skip the check. When the Lab is not up
    yet, neither: the next load asks again and the badge still shows.
    """
    cookies = handler.headers.get("Cookie") or ""
    if any(p.strip().startswith(ONBOARDED_COOKIE + "=") for p in cookies.split(";")):
        return None, False
    tab = next((t for t in TABS if t["badge"] and t["prefix"] != "/"), None)
    if tab is None:
        return None, False
    headers = {"Accept": "application/json"}
    token = os.environ.get(tab["bearer_env"], "").strip() if tab["bearer_env"] else ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    path = f"/{tab['badge']}" if tab["strip"] else f"{PREFIX}{tab['prefix']}/{tab['badge']}"
    conn = HTTPConnection(UPSTREAM_HOST, tab["port"], timeout=4)
    try:
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        data = json.loads(resp.read()) if resp.status == 200 else None
    except (OSError, ValueError):
        return None, False
    finally:
        conn.close()
    if not isinstance(data, dict):
        return None, False
    if data.get("kind") == "new" and not data.get("ready"):
        return tab_href(tab), True
    return None, True


def onboarded_cookie() -> str:
    return f"{ONBOARDED_COOKIE}=1; Path={PREFIX or ''}/; Max-Age=2592000; SameSite=Lax"


def is_index_path(path: str) -> bool:
    route = urlparse(path).path or "/"
    return route in ("/", "") or bool(PREFIX and route in (PREFIX, f"{PREFIX}/"))


def index_token_redirect(path: str, cookie_header: str | None) -> str | None:
    """Connect lands on ``/`` with no token: send it to dsh's launch-token URL."""
    if not is_index_path(path):
        return None
    query = parse_qs(urlparse(path).query, keep_blank_values=True)
    if query.get("token") or has_dsh_auth_cookie(cookie_header):
        return None
    token = read_launch_token()
    if not token:
        return None
    query["token"] = [token]
    return urlunparse(("", "", f"{PREFIX}/", "", urlencode(query, doseq=True), ""))


def rewrite_set_cookie(value: str) -> str:
    """dsh's Strict cookies would be dropped after the portal → workloads hop."""
    return re.sub(r"(?i)SameSite=Strict", "SameSite=Lax", value)


def rewrite_location(value: str) -> str:
    if not PREFIX or not value.startswith("/") or value.startswith("//"):
        return value
    if value == PREFIX or value.startswith((PREFIX + "/", PREFIX + "?")):
        return value
    return PREFIX + value


# ---------------------------------------------------------------------------
# dsh body rewrite (absolute URLs under the Skaha session path)
# ---------------------------------------------------------------------------

REWRITE_TYPES = (
    "text/html",
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/x-javascript",
)  # never JSON: dsh returns file contents and transcripts as JSON
DSH_ABS_PREFIXES = ("/api/", "/assets/", "/favicon", "/plugins/", "/open-in-app/")

# Keep dsh's channel string "/api"; only network URLs get the session prefix.
API_SHIM = """<script data-astroai-api-shim>
(function () {
  var P = {prefix};
  if (!P) return;
  var T = globalThis.__DSH_TRANSPORT__ || (globalThis.__DSH_TRANSPORT__ = {});
  T.ownsHost = true;
  function needs(p) {
    return (p === "/api" || p.indexOf("/api/") === 0 || p.indexOf("/plugins/") === 0 ||
            p.indexOf("/open-in-app/") === 0) && p.indexOf(P + "/") !== 0;
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
    if (typeof input === "string") input = rewrite(input);
    else if (input instanceof URL) input = new URL(rewrite(input.href));
    else if (typeof Request !== "undefined" && input instanceof Request)
      input = new Request(rewrite(input.url), input);
    return F.call(this, input, init);
  };
  function wrap(C) {
    function W(url, arg) {
      return arg === undefined ? new C(rewrite(url)) : new C(rewrite(url), arg);
    }
    W.prototype = C.prototype;
    ["CONNECTING", "OPEN", "CLOSING", "CLOSED"].forEach(function (k) {
      if (k in C) W[k] = C[k];
    });
    return W;
  }
  window.WebSocket = wrap(window.WebSocket);
  if (typeof window.EventSource === "function") window.EventSource = wrap(window.EventSource);
})();
</script>"""


def _after_head(text: str, snippet: str) -> str:
    head = text.lower().find("<head")
    gt = text.find(">", head) if head >= 0 else -1
    return text[: gt + 1] + snippet + text[gt + 1 :] if gt >= 0 else snippet + text


def rewrite_dsh_body(data: bytes, content_type: str) -> bytes:
    ctype = content_type.split(";", 1)[0].strip().lower()
    if ctype not in REWRITE_TYPES:
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    if PREFIX:
        for p in DSH_ABS_PREFIXES:
            for q in ('"', "'", "`"):
                text = text.replace(f"{q}{PREFIX}{p}", f"{q}__KEEP__{p}")
                text = text.replace(f"{q}{p}", f"{q}{PREFIX}{p}")
                text = text.replace(f"{q}__KEEP__{p}", f"{q}{PREFIX}{p}")
    if ctype == "text/html":
        if PREFIX:
            for q in ('"', "'"):
                text = text.replace(f"<base href={q}/{q}", f"<base href={q}{PREFIX}/{q}")
            if "data-astroai-api-shim" not in text:
                text = _after_head(text, API_SHIM.replace("{prefix}", json.dumps(PREFIX)))
        text = stick_html_title(text, BRAND_TITLE)
    return text.encode("utf-8")


# ---------------------------------------------------------------------------
# Tab strip
# ---------------------------------------------------------------------------

TAB_HEIGHT = 30

# Shadow root keeps host CSS out; same-origin script so page CSPs that block
# inline script still run it. The page is pushed down by TAB_HEIGHT.
TABS_JS = r"""(function () {
  if (window.__astroaiTabs) return;
  window.__astroaiTabs = true;
  var el = document.getElementById("astroai-tabs-config");
  if (!el) return;
  var cfg;
  try { cfg = JSON.parse(el.textContent); } catch (e) { return; }
  var H = cfg.height;
  var host = document.createElement("div");
  host.setAttribute("data-astroai-tabs", "");
  host.style.cssText = "position:fixed;top:0;left:0;right:0;height:" + H + "px;z-index:2147483646;";
  var root = host.attachShadow({ mode: "open" });
  root.innerHTML =
    "<style>" +
    ":host{all:initial}" +
    "nav{box-sizing:border-box;height:" + H + "px;display:flex;align-items:stretch;gap:2px;" +
    "padding:0 8px;font:500 13px/1 system-ui,-apple-system,'Segoe UI',sans-serif;" +
    "background:#0f1222;color:#a6accd;border-bottom:1px solid #262a40}" +
    ".brand{display:flex;align-items:center;padding:0 10px 0 2px;color:#e4e6f5;font-weight:650}" +
    "a{display:flex;align-items:center;gap:6px;padding:0 12px;color:inherit;text-decoration:none;" +
    "border-bottom:2px solid transparent}" +
    "a:hover{color:#e4e6f5;background:#1a1e33}" +
    "a:focus-visible{outline:2px solid #7aa2f7;outline-offset:-2px}" +
    "a[aria-current=page]{color:#fff;border-bottom-color:#7aa2f7}" +
    ".b{min-width:16px;height:16px;padding:0 4px;box-sizing:border-box;border-radius:8px;" +
    "background:#e0af68;color:#1a1b26;font-size:11px;font-weight:700;display:none;" +
    "align-items:center;justify-content:center}" +
    ".b.on{display:inline-flex}" +
    "@media (prefers-color-scheme: light){" +
    "nav{background:#f4f5fa;color:#4c5068;" +
    "border-bottom-color:#d8dbe8}.brand{color:#1c1e2b}a:hover{background:#e6e8f2;color:#1c1e2b}" +
    "a[aria-current=page]{color:#1c1e2b;border-bottom-color:#3d59a1}}" +
    "</style><nav aria-label='Session'></nav>";
  var nav = root.querySelector("nav");
  var brand = document.createElement("span");
  brand.className = "brand";
  brand.textContent = cfg.brand;
  nav.appendChild(brand);
  var badges = {};
  cfg.tabs.forEach(function (t) {
    var a = document.createElement("a");
    a.href = t.href;
    a.textContent = t.label;
    if (t.id === cfg.active) a.setAttribute("aria-current", "page");
    if (t.badge) {
      var b = document.createElement("span");
      b.className = "b";
      a.appendChild(b);
      badges[t.id] = { el: b, a: a, url: t.href + t.badge, tab: t };
    }
    nav.appendChild(a);
  });
  var style = document.createElement("style");
  style.setAttribute("data-astroai-tabs-style", "");
  style.textContent =
    "html{scroll-padding-top:" + H + "px}" +
    "body{margin-top:" + H + "px!important;height:calc(100% - " + H + "px)!important}" +
    "body>#root,body>#app,body>main{height:calc(100dvh - " + H + "px)!important;" +
    "min-height:0!important;max-height:calc(100dvh - " + H + "px)!important}";
  (document.head || document.documentElement).appendChild(style);
  function mount() { document.body.insertBefore(host, document.body.firstChild); }
  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount);

  Object.keys(badges).forEach(function (id) {
    var s = badges[id];
    var timer = null;
    function poll() {
      fetch(s.url, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          var n = d && !d.ready ? Number(d.pending) || 0 : 0;
          s.el.textContent = n ? String(n) : "";
          s.el.classList.toggle("on", n > 0);
          s.a.title = n ? n + " step" + (n === 1 ? "" : "s") + " left to set up this session" : "";
          timer = setTimeout(poll, n ? 5000 : 60000);
        })
        .catch(function () { timer = setTimeout(poll, 15000); });
    }
    poll();
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") { clearTimeout(timer); poll(); }
    });
  });
})();
"""


def tabs_snippet(active: str) -> str:
    cfg = {
        "brand": BRAND_TITLE,
        "height": TAB_HEIGHT,
        "active": active,
        "tabs": [
            {"id": t["id"], "label": t["label"], "href": tab_href(t), "badge": t["badge"]}
            for t in TABS
        ],
    }
    payload = json.dumps(cfg).replace("</", "<\\/")
    return (
        f'<script id="astroai-tabs-config" type="application/json">{payload}</script>'
        f'<script src="{PREFIX}{ASSET_ROUTE}" defer></script>'
    )


def inject_tabs(data: bytes, content_type: str, active: str) -> bytes:
    if content_type.split(";", 1)[0].strip().lower() != "text/html":
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    if "astroai-tabs-config" in text:
        return data
    if 'data-astroai-proxy-rev="' not in text:
        text = _after_head(text, f'<meta data-astroai-proxy-rev="{PROXY_REVISION}" />')
    snippet = tabs_snippet(active)
    idx = text.lower().rfind("</body>")
    text = text[:idx] + snippet + text[idx:] if idx >= 0 else text + snippet
    return text.encode("utf-8")


# ---------------------------------------------------------------------------
# Forwarding
# ---------------------------------------------------------------------------

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
}

STARTING_HTML = (
    b"<!DOCTYPE html><html><head><meta charset='utf-8'/>"
    b"<meta http-equiv='refresh' content='3'/><title>AstroAI</title></head>"
    b"<body style='font-family:system-ui,sans-serif;padding:2rem;line-height:1.5;"
    b"background:#0f1222;color:#e4e6f5'><h1>Your session is starting</h1>"
    b"<p>This page refreshes by itself. It usually takes under a minute.</p></body></html>"
)


def unavailable_html(label: str) -> bytes:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
        "<meta http-equiv='refresh' content='5'/></head>"
        "<body style='font-family:system-ui,sans-serif;padding:2rem;background:#0f1222;"
        f"color:#e4e6f5'><h1>{label} is starting</h1>"
        "<p>This page refreshes by itself.</p>"
        f"<p><a href='{PREFIX}/' style='color:#7aa2f7'>Back to the Agent tab</a></p>"
        "</body></html>"
    ).encode()


def _send(handler: BaseHTTPRequestHandler, status: int, body: bytes, ctype: str) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")  # any request body is left unread
    handler.end_headers()
    handler.close_connection = True
    if handler.command != "HEAD":
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            handler.wfile.write(body)


def _redirect(
    handler: BaseHTTPRequestHandler, location: str, cookies: tuple[str, ...] | list[str] = ()
) -> None:
    handler.send_response(302, "Found")
    handler.send_header("Location", location)
    handler.send_header("Cache-Control", "no-store")
    for cookie in cookies:
        handler.send_header("Set-Cookie", cookie)
    handler.send_header("Content-Length", "0")
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.close_connection = True


def port_open(port: int) -> bool:
    try:
        with socket.create_connection((UPSTREAM_HOST, port), timeout=0.2):
            return True
    except OSError:
        return False


def studio_status() -> dict[str, Any]:
    services = {
        t["id"]: {"name": t["label"], "port": t["port"], "up": port_open(t["port"])} for t in TABS
    }
    return {
        "status": "ready" if any(s["up"] for s in services.values()) else "starting",
        "image": "dsh",
        "session_id": SESSION_ID or None,
        "prefix": PREFIX or None,
        "services": services,
    }


def tab_location(tab: dict[str, Any], value: str) -> str:
    """Absolute redirects from a stripped tab go back under that tab's prefix."""
    if tab["prefix"] == "/" or not tab["strip"]:
        return rewrite_location(value)
    if value.startswith("/") and not value.startswith("//"):
        return f"{PREFIX}{tab['prefix']}{value}"
    return value


def upstream_path(path: str) -> str:
    """Strip Skaha's ``/session/contrib/<id>``, also when it appears twice.

    dsh joins some paths onto ``document.baseURI`` (``new URL("/api/remote.mux"
    .slice(1), baseURI)``); after the body rewrite that string already carries
    the prefix, so the browser asks for ``<prefix><prefix>/api/...``.
    """
    if not PREFIX:
        return path
    parsed = urlparse(path)
    route = parsed.path or "/"
    if not (route == PREFIX or route.startswith(PREFIX + "/")):
        return path
    while route == PREFIX or route.startswith(PREFIX + "/"):
        route = route[len(PREFIX) :] or "/"
    return urlunparse(("", "", route, "", parsed.query, ""))


def _upstream_headers(handler: BaseHTTPRequestHandler, tab: dict[str, Any]) -> dict[str, str]:
    headers = {
        k: v
        for k, v in handler.headers.items()
        if k.lower() not in HOP_BY_HOP
        and k.lower() not in ("host", "accept-encoding", "x-forwarded-prefix")
    }
    host = handler.headers.get("Host") or handler.headers.get("X-Forwarded-Host")
    if host:
        headers["Host"] = host.split(",", 1)[0].strip()
    headers["Accept-Encoding"] = "identity"
    if tab["prefix"] != "/" and tab["strip"]:
        headers["X-Forwarded-Prefix"] = f"{PREFIX}{tab['prefix']}"
    if tab["bearer_env"]:
        for k in [k for k in headers if k.lower() == "authorization"]:
            del headers[k]
        token = os.environ.get(tab["bearer_env"], "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def _splice(client: socket.socket, upstream: socket.socket) -> None:
    pair = [client, upstream]
    with contextlib.suppress(OSError):
        while True:
            readable, _, _ = select.select(pair, [], [], 300)
            for src in readable:
                data = src.recv(65536)
                if not data:
                    return
                (upstream if src is client else client).sendall(data)


def forward_websocket(
    handler: BaseHTTPRequestHandler, port: int, path: str, headers: dict[str, str]
) -> None:
    try:
        upstream = socket.create_connection((UPSTREAM_HOST, port), timeout=30)
    except OSError as exc:
        handler.send_error(502, f"upstream unreachable: {exc}")
        return
    lines = [f"{handler.command} {path} HTTP/1.1"]
    lines += [f"{k}: {v}" for k, v in headers.items()]
    lines += ["Connection: Upgrade", f"Upgrade: {handler.headers.get('Upgrade', 'websocket')}"]
    try:
        upstream.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1"))
        upstream.settimeout(None)
        _splice(handler.connection, upstream)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            upstream.close()
        handler.close_connection = True


def wants_tabs(handler: BaseHTTPRequestHandler, tab_route: str) -> bool:
    """Only top-level pages get the strip, never iframes or previewed user files."""
    dest = handler.headers.get("Sec-Fetch-Dest")
    if dest:
        return dest == "document"
    return tab_route in ("", "/")


def forward(
    handler: BaseHTTPRequestHandler,
    tab: dict[str, Any],
    path: str,
    *,
    tab_route: str = "/",
    set_cookies: list[str] | None = None,
) -> None:
    headers = _upstream_headers(handler, tab)
    conn_hdr = handler.headers.get("Connection", "").lower()
    if "upgrade" in conn_hdr and handler.headers.get("Upgrade", "").lower() == "websocket":
        forward_websocket(handler, tab["port"], path, headers)
        return

    if "chunked" in handler.headers.get("Transfer-Encoding", "").lower():
        _send(handler, 411, b"Send a Content-Length.", "text/plain; charset=utf-8")
        return
    try:
        length = int(handler.headers.get("Content-Length", "0") or "0")
    except ValueError:
        length = -1
    if length < 0:
        _send(handler, 400, b"Bad Content-Length.", "text/plain; charset=utf-8")
        return
    body = handler.rfile.read(length) if length > 0 else None
    is_dsh = tab["prefix"] == "/"
    conn = HTTPConnection(UPSTREAM_HOST, tab["port"], timeout=600)
    try:
        conn.request(handler.command, path, body=body, headers=headers)
        upstream = conn.getresponse()
    except OSError as exc:
        conn.close()
        handler.log_message('"unavailable" %s %s (%s)', tab["id"], path, exc)
        if is_dsh and is_index_path(handler.path):
            _send(handler, 200, STARTING_HTML, "text/html; charset=utf-8")
        else:
            _send(handler, 503, unavailable_html(tab["label"]), "text/html; charset=utf-8")
        return

    ctype = upstream.getheader("Content-Type") or ""
    streaming = ctype.lower().startswith("text/event-stream") or (
        "text/event-stream" in handler.headers.get("Accept", "")
    )

    # A stale dsh cookie answers 401 on the index: drop it and re-run Connect.
    if is_dsh and upstream.status == 401 and is_index_path(handler.path):
        loc = index_token_redirect(path, None)
        if loc and not parse_qs(urlparse(path).query).get("token"):
            upstream.read()
            conn.close()
            _redirect(handler, loc, expire_dsh_auth_cookies(handler.headers.get("Cookie")))
            handler.log_message('"auth-recovery" %s', handler.path)
            return

    head = handler.command == "HEAD"
    raw = b""
    if not streaming and not head:
        raw = upstream.read()
        if is_dsh:
            raw = rewrite_dsh_body(raw, ctype)
        if 200 <= upstream.status < 300 and wants_tabs(handler, tab_route):
            raw = inject_tabs(raw, ctype, tab["id"])

    handler.send_response(upstream.status, upstream.reason)
    for key, value in upstream.getheaders():
        lk = key.lower()
        if lk in HOP_BY_HOP or lk == "host":
            continue
        if lk == "location":
            value = tab_location(tab, value)
        elif lk == "set-cookie" and is_dsh:
            value = rewrite_set_cookie(value)
        handler.send_header(key, value)
    for cookie in set_cookies or ():
        handler.send_header("Set-Cookie", cookie)
    if streaming:
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("X-Accel-Buffering", "no")
    elif head:
        if upstream.getheader("Content-Length") is not None:
            handler.send_header("Content-Length", upstream.getheader("Content-Length"))
    else:
        handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.close_connection = True

    try:
        if handler.command == "HEAD":
            return
        if not streaming:
            handler.wfile.write(raw)
            return
        while True:
            chunk = upstream.read1(8192) if hasattr(upstream, "read1") else upstream.read(8192)
            if not chunk:
                break
            handler.wfile.write(chunk)
            handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        conn.close()


class DshProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("dsh-proxy: %s\n" % (fmt % args))

    def _handle(self) -> None:
        public = upstream_path(self.path)
        parsed = urlparse(public)
        route = parsed.path or "/"

        if route == STATUS_ROUTE:
            _send(self, 200, json.dumps(studio_status()).encode(), "application/json")
            return
        if route == ASSET_ROUTE and self.command in ("GET", "HEAD"):
            self.send_response(200)
            body = TABS_JS.encode()
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return

        tab = match_tab(route)
        if tab["prefix"] == "/":
            if self.command in ("GET", "HEAD"):
                loc = index_token_redirect(self.path, self.headers.get("Cookie"))
                if loc is not None:
                    _redirect(self, loc)
                    return
                if (
                    is_index_path(self.path)
                    and not has_dsh_auth_cookie(self.headers.get("Cookie"))
                    and not read_launch_token()
                ):
                    _send(self, 200, STARTING_HTML, "text/html; charset=utf-8")
                    return
            extra: list[str] = []
            if (
                self.command == "GET"
                and is_index_path(self.path)
                and has_dsh_auth_cookie(self.headers.get("Cookie"))
                and self.headers.get("Sec-Fetch-Dest", "document") == "document"
            ):
                loc, remember = onboarding_redirect(self)
                if loc:
                    _redirect(self, loc, [onboarded_cookie()])
                    return
                if remember:
                    extra.append(onboarded_cookie())
            forward(self, tab, public, tab_route=route, set_cookies=extra)
            return

        p = tab["prefix"]
        if route == p:
            query = f"?{parsed.query}" if parsed.query else ""
            _redirect(self, f"{PREFIX}{p}/{query}")
            return
        if tab["strip"]:
            rest = route[len(p) :] or "/"
            target = urlunparse(("", "", rest, "", parsed.query, ""))
        else:
            rest = route[len(p) :] or "/"
            target = f"{PREFIX}{public}"
        forward(self, tab, target, tab_route=rest)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _handle  # noqa: N815


def main() -> int:
    server = ThreadingHTTPServer(("0.0.0.0", PUBLIC_PORT), DshProxyHandler)
    server.daemon_threads = True
    routes = " ".join(f"{t['prefix']}→:{t['port']}" for t in TABS)
    sys.stderr.write(
        f"dsh-proxy: listening 0.0.0.0:{PUBLIC_PORT} {routes} prefix={PREFIX or '(none)'}\n"
    )
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
