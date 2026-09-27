"""Reverse-proxy and multi-service ingress for CANFAR contributed Studio sessions.

Multiplexes the unified 5-in-1 workbench over port 5000:
  * ``/`` → DeepSeek Harness agent SPA (127.0.0.1:DSH_PORT, default 3080)
  * ``/terminal/*`` & ``/astroai-terminal/*`` → ghostty-web terminal (127.0.0.1:TERMINAL_PORT, default 4793)
  * ``/jupyter/*`` → JupyterLab 4 (127.0.0.1:JUPYTER_PORT, default 8888)
  * ``/marimo/*`` → Marimo reactive notebooks (127.0.0.1:MARIMO_PORT, default 2718)
  * ``/vscode/*`` → OpenVSCode Server (127.0.0.1:VSCODE_PORT, default 8080)
  * ``/hub/*`` & ``/astroai-agents/*`` → Compute & Agent Wizard hub (127.0.0.1:WIZARD_PORT, default 4792)
  * ``/api/studio/status`` → Real-time JSON health and system metrics

Features:
  * Early 0.0.0.0:5000 bind with instant 200 splash page (prevents Skaha liveness crash-loops)
  * Transparent bi-directional WebSocket splicing across all subservices
  * Injects modern Glassmorphism Command Dock into served HTML pages
  * Dynamic path rewriting and dsh /api fetch/WebSocket shim for /session/contrib/<id>
  * Zero external dependencies (Python standard library only)
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import select
import shutil
import socket
import sys
from collections.abc import Callable
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
DSH_HOST = os.environ.get("DSH_HOST", "127.0.0.1")
DSH_PORT = int(os.environ.get("DSH_PORT", "3080"))
WIZARD_HOST = os.environ.get("ASTROAI_AGENT_WIZARD_HOST", "127.0.0.1")
WIZARD_PORT = int(os.environ.get("ASTROAI_AGENT_WIZARD_PORT", "4792"))
TERMINAL_HOST = os.environ.get("ASTROAI_TERMINAL_HOST", "127.0.0.1")
TERMINAL_PORT = int(os.environ.get("ASTROAI_TERMINAL_PORT", "4793"))
JUPYTER_HOST = os.environ.get("ASTROAI_JUPYTER_HOST", "127.0.0.1")
JUPYTER_PORT = int(os.environ.get("ASTROAI_JUPYTER_PORT", "8888"))
MARIMO_HOST = os.environ.get("ASTROAI_MARIMO_HOST", "127.0.0.1")
MARIMO_PORT = int(os.environ.get("ASTROAI_MARIMO_PORT", "2718"))
VSCODE_HOST = os.environ.get("ASTROAI_VSCODE_HOST", "127.0.0.1")
VSCODE_PORT = int(os.environ.get("ASTROAI_VSCODE_PORT", "8080"))

SESSION_ID = (os.environ.get("skaha_sessionid") or "").strip()  # noqa: SIM112
PREFIX = f"/session/contrib/{SESSION_ID}" if SESSION_ID else ""
WIZARD_MOUNT = "/astroai-agents"
TERMINAL_MOUNT = "/astroai-terminal"
COOKIE_PREFIX = "dsh-auth-"
BRAND_TITLE = "AstroAI Studio"
PROXY_REVISION = "11"


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

ABS_PREFIXES = (
    "/api/",
    "/assets/",
    "/favicon",
    "/plugins/",
    "/terminal",
    "/jupyter",
    "/marimo",
    "/vscode",
    "/hub",
    "/astroai-agents",
    "/astroai-terminal",
)

# Keep channel as "/api"; only network URLs get the session prefix.
API_SHIM = """<script data-astroai-api-shim>
(function () {
  var P = {prefix};
  if (!P) return;
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
    return API_SHIM.replace("{prefix}", json.dumps(PREFIX))


# Rendered in a shadow root so host-page CSS and document-level link
# interceptors (dsh opens anchors in new tabs) cannot reach the dock.
COMMAND_DOCK_TEMPLATE = """
<div id="astroai-studio-dock" data-astroai-dock></div>
<template id="astroai-studio-dock-tpl">
  <style>
    :host { all: initial; }
    * { box-sizing: border-box; }
    .root {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      font-size: 13px;
      line-height: 1.3;
      color: #cdd6f4;
    }
    .dock {
      position: fixed;
      left: 50%;
      transform: translateX(-50%);
      z-index: 2147483646;
      user-select: none;
      -webkit-user-select: none;
    }
    .bar .dock { top: 10px; }
    .mini .dock { bottom: 28px; display: flex; flex-direction: column-reverse; align-items: center; gap: 6px; }
    .pill {
      display: flex;
      align-items: center;
      gap: 3px;
      background: rgba(22, 25, 37, 0.94);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border: 1px solid rgba(255, 255, 255, 0.16);
      border-radius: 9999px;
      padding: 3px 5px 3px 12px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45), 0 2px 6px rgba(0, 0, 0, 0.2);
    }
    .mini .pill { display: none; }
    .mini.open .pill { display: flex; }
    .brand { font-weight: 700; color: #89b4fa; margin-right: 6px; font-size: 12px; }
    .spark { color: #f5c2e7; }
    a.link {
      display: flex;
      align-items: center;
      gap: 5px;
      padding: 5px 9px;
      border: 1px solid transparent;
      border-radius: 9999px;
      color: #cdd6f4;
      text-decoration: none;
      font-weight: 500;
      white-space: nowrap;
    }
    a.link:hover { background: rgba(255, 255, 255, 0.12); color: #fff; }
    a.link.active { background: rgba(137, 180, 250, 0.22); color: #89b4fa; border-color: rgba(137, 180, 250, 0.4); }
    .sep { width: 1px; height: 14px; background: rgba(255, 255, 255, 0.12); margin: 0 4px; }
    button {
      font: inherit;
      color: #a6adc8;
      background: none;
      border: none;
      cursor: pointer;
      border-radius: 9999px;
    }
    button:hover { color: #fff; background: rgba(255, 255, 255, 0.1); }
    .help-btn { width: 26px; height: 26px; font-weight: 700; }
    .handle {
      display: none;
      padding: 4px 12px;
      font-size: 12px;
      font-weight: 600;
      color: #cdd6f4;
      background: rgba(22, 25, 37, 0.85);
      border: 1px solid rgba(255, 255, 255, 0.16);
      box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
    }
    .handle:hover { background: rgba(22, 25, 37, 0.97); }
    .mini .handle { display: block; }
    .overlay {
      position: fixed;
      inset: 0;
      z-index: 2147483647;
      display: none;
      align-items: center;
      justify-content: center;
      background: rgba(10, 12, 20, 0.55);
    }
    .overlay.show { display: flex; }
    .card {
      width: min(560px, calc(100vw - 32px));
      max-height: calc(100vh - 48px);
      overflow: auto;
      background: #1e2030;
      border: 1px solid rgba(255, 255, 255, 0.14);
      border-radius: 14px;
      padding: 22px 24px 18px;
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
    }
    h2 { margin: 0 0 4px; font-size: 18px; color: #fff; }
    .lead { margin: 0 0 14px; color: #a6adc8; }
    .tools { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; }
    .tools a {
      display: grid;
      grid-template-columns: 26px 96px 1fr;
      align-items: baseline;
      padding: 7px 8px;
      border-radius: 8px;
      color: #cdd6f4;
      text-decoration: none;
    }
    .tools a:hover { background: rgba(255, 255, 255, 0.07); }
    .tools b { color: #fff; font-weight: 600; }
    .tools span.d { color: #a6adc8; }
    .files {
      margin: 14px 0 0;
      padding: 10px 12px;
      border-radius: 8px;
      background: rgba(245, 169, 127, 0.1);
      border: 1px solid rgba(245, 169, 127, 0.3);
      color: #f4dbd6;
    }
    code {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
      color: #f5e0dc;
      background: rgba(255, 255, 255, 0.08);
      padding: 1px 5px;
      border-radius: 4px;
    }
    .actions { display: flex; justify-content: flex-end; margin-top: 16px; }
    .ok { padding: 7px 16px; color: #1e2030; background: #89b4fa; font-weight: 600; }
    .ok:hover { color: #1e2030; background: #b4cefb; }
  </style>
  <div class="root">
    <div class="dock">
      <div class="pill">
        <div class="brand"><span class="spark">✦</span> Studio</div>
        <a href="{prefix}/" id="astroai-agents-chip" class="link" data-tool="agent" title="AI coding agent"><span>🤖</span><span>Agents</span></a>
        <a href="{prefix}/terminal/" id="astroai-terminal-chip" class="link" data-tool="terminal" title="Shell in this session"><span>💻</span><span>Terminal</span></a>
        <a href="{prefix}/jupyter/lab" id="astroai-jupyter-chip" class="link" data-tool="jupyter" title="JupyterLab notebooks"><span>🪐</span><span>JupyterLab</span></a>
        <a href="{prefix}/marimo/" id="astroai-marimo-chip" class="link" data-tool="marimo" title="Reactive Python notebooks"><span>⚡</span><span>Marimo</span></a>
        <a href="{prefix}/vscode/" id="astroai-vscode-chip" class="link" data-tool="vscode" title="Code editor"><span>📝</span><span>VS Code</span></a>
        <div class="sep"></div>
        <a href="{prefix}/hub/" id="astroai-hub-chip" class="link" data-tool="hub" title="Batch jobs and Ray clusters"><span>🚀</span><span>Compute</span></a>
        <button class="help-btn" data-help title="What can I do here?">?</button>
      </div>
      <button class="handle" title="Switch Studio tool">✦ Studio</button>
    </div>
    <div class="overlay" role="dialog" aria-modal="true" aria-label="Welcome to AstroAI Studio">
      <div class="card">
        <h2>Welcome to AstroAI Studio</h2>
        <p class="lead">One CANFAR session, several tools sharing the same files. Switch tools from the Studio bar.</p>
        <ul class="tools">
          <li><a href="{prefix}/"><span>🤖</span><b>Agents</b><span class="d">Click <i>Choose workspace</i>, pick a folder, then describe a task in plain words.</span></a></li>
          <li><a href="{prefix}/terminal/"><span>💻</span><b>Terminal</b><span class="d">Bash shell. Try <code>canfar-lab status</code> or <code>canfar-lab clone &lt;repo&gt;</code>.</span></a></li>
          <li><a href="{prefix}/jupyter/lab"><span>🪐</span><b>JupyterLab</b><span class="d">Notebooks with a Python 3 kernel; browse <code>/arc</code> for your stored data.</span></a></li>
          <li><a href="{prefix}/marimo/"><span>⚡</span><b>Marimo</b><span class="d">Reactive Python notebooks; open <code>notebooks/starter.py</code> to begin.</span></a></li>
          <li><a href="{prefix}/vscode/"><span>📝</span><b>VS Code</b><span class="d">Full editor in the browser.</span></a></li>
          <li><a href="{prefix}/hub/"><span>🚀</span><b>Compute</b><span class="d">Run headless batch jobs and Ray clusters beyond this session.</span></a></li>
        </ul>
        <div class="files">
          Working folder: <code data-workdir>…</code><span data-scratch-note hidden> is <b>temporary</b> and is deleted when the session ends.</span><br>
          Keep results in <code data-home>/arc/home/$USER</code> or <code>/arc/projects/&lt;project&gt;</code>, or run <code>canfar-lab save</code>.
        </div>
        <div class="actions"><button class="ok" data-close>Got it</button></div>
      </div>
    </div>
  </div>
</template>
<script>
(function () {
  var host = document.getElementById('astroai-studio-dock');
  var tpl = document.getElementById('astroai-studio-dock-tpl');
  if (!host || !tpl || host.shadowRoot) return;
  var root = host.attachShadow({ mode: 'open' });
  root.appendChild(tpl.content.cloneNode(true));
  var shell = root.querySelector('.root');
  shell.classList.add('{mode}');
  var P = {prefix_json};
  var here = location.pathname;
  root.querySelectorAll('a.link').forEach(function (a) {
    var href = a.getAttribute('href');
    var home = href === P + '/';
    if (home ? (here === href || here === P) : here.indexOf(href.replace(/lab$/, '')) === 0) {
      a.classList.add('active');
    }
  });
  root.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href]');
    if (a && !(e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0)) {
      e.preventDefault();
      e.stopPropagation();
      location.assign(a.getAttribute('href'));
    }
  });
  var overlay = root.querySelector('.overlay');
  var KEY = 'astroai-studio-welcome-v1';
  var filled = false;
  function fillPaths() {
    if (filled) return;
    filled = true;
    fetch(P + '/api/studio/status', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        if (!s) return;
        if (s.workdir) root.querySelector('[data-workdir]').textContent = s.workdir;
        if (s.home && s.home.indexOf('/arc/') === 0) root.querySelector('[data-home]').textContent = s.home;
        root.querySelector('[data-scratch-note]').hidden = !s.workdir_ephemeral;
      })
      .catch(function () {});
  }
  function openHelp() { fillPaths(); overlay.classList.add('show'); }
  function closeHelp() {
    overlay.classList.remove('show');
    try { localStorage.setItem(KEY, '1'); } catch (err) {}
  }
  root.querySelector('[data-help]').addEventListener('click', openHelp);
  root.querySelector('[data-close]').addEventListener('click', closeHelp);
  overlay.addEventListener('click', function (e) { if (e.target === overlay) closeHelp(); });
  root.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeHelp(); });
  var handle = root.querySelector('.handle');
  handle.addEventListener('click', function () { shell.classList.toggle('open'); });
  document.addEventListener('click', function (e) {
    if (e.target !== host) shell.classList.remove('open');
  });
  var seen = false;
  try { seen = localStorage.getItem(KEY) === '1'; } catch (err) {}
  if ('{mode}' === 'bar' && !seen) openHelp();
})();
</script>
"""


def command_dock_html(mode: str = "bar") -> str:
    """``bar``: full top bar (agent page). ``mini``: bottom handle over embedded tools."""
    return (
        COMMAND_DOCK_TEMPLATE.replace("{prefix_json}", json.dumps(PREFIX))
        .replace("{prefix}", PREFIX)
        .replace("{mode}", mode)
    )


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
        headers.append(f"{name}=; Max-Age=0; Path=/")
        if PREFIX:
            headers.append(f"{name}=; Max-Age=0; Path={PREFIX}/")
    return headers


def rewrite_set_cookie(value: str) -> str:
    """Make dsh auth cookies usable after Skaha portal → workloads Connect."""
    return re.sub(r"(?i)SameSite=Strict", "SameSite=Lax", value)


def index_token_redirect(path: str, cookie_header: str | None) -> str | None:
    """If Skaha hit ``/`` without ``?token=``, redirect to the dsh launch token URL."""
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
        text = _add_dock(text, "bar")
    return text.encode("utf-8")


def _add_dock(text: str, mode: str) -> str:
    if 'data-astroai-proxy-rev="' not in text:
        text = text.replace(
            "<head>",
            f'<head><meta data-astroai-proxy-rev="{PROXY_REVISION}" />',
            1,
        )
    if "data-astroai-dock" not in text:
        dock = command_dock_html(mode)
        idx = text.lower().rfind("</body>")
        text = text[:idx] + dock + text[idx:] if idx >= 0 else text + dock
    return text


def inject_dock(data: bytes, content_type: str) -> bytes:
    """Tools already served under the session base path: add the dock, rewrite nothing."""
    if content_type.split(";", 1)[0].strip().lower() != "text/html":
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return _add_dock(text, "mini").encode("utf-8")


def rewrite_location(value: str) -> str:
    """Keep absolute Locations under the Skaha session path."""
    if not PREFIX or not value.startswith("/"):
        return value
    if value == PREFIX or value.startswith((PREFIX + "/", PREFIX + "?")):
        return value
    return PREFIX + value


def upstream_path(path: str) -> str:
    """Strip Skaha ``/session/contrib/<id>`` before forwarding."""
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
    browser_host = handler.headers.get("Host") or handler.headers.get("X-Forwarded-Host")
    if browser_host:
        headers["Host"] = browser_host.split(",", 1)[0].strip()
    headers["Accept-Encoding"] = "identity"
    return headers


STARTING_HTML = (
    b"<!DOCTYPE html><html><head>"
    b'<meta charset="utf-8"/>'
    b'<meta http-equiv="refresh" content="3"/>'
    b"<title>AstroAI Studio</title></head>"
    b"<body style='font-family:system-ui,-apple-system,sans-serif;padding:2rem;line-height:1.5;background:#181926;color:#cad3f5'>"
    b"<h1>AstroAI Studio is starting</h1>"
    b"<p>Preparing the coding workbench and tools &mdash; this page refreshes automatically.</p>"
    b"</body></html>"
)


def _is_index_path(path: str) -> bool:
    route = urlparse(path).path or "/"
    return route in ("/", "") or bool(PREFIX and (route == PREFIX or route == f"{PREFIX}/"))


def _send_html(handler: BaseHTTPRequestHandler, status: int, body: bytes) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if handler.command != "HEAD":
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            handler.wfile.write(body)


def _send_json(handler: BaseHTTPRequestHandler, status: int, data: Any) -> None:
    body = json.dumps(data, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if handler.command != "HEAD":
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            handler.wfile.write(body)


def _check_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _studio_workdir() -> str | None:
    """Working folder written by startup-studio.sh (resolved after the proxy starts)."""
    state = os.environ.get("ASTROAI_STUDIO_STATE", "").strip().rstrip("/")
    if not state:
        return None
    try:
        return Path(f"{state}/studio-cwd").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _is_ephemeral(path: str) -> bool:
    """Only /arc (home and projects) outlives the session; /srcdir and scratch do not."""
    home = os.path.expanduser("~").rstrip("/")
    persistent = ("/arc/", f"{home}/") if home.startswith("/arc/") else ("/arc/",)
    return not (path.rstrip("/") + "/").startswith(persistent)


def get_studio_status() -> dict[str, Any]:
    """Collect real-time health and system telemetry for the Studio session."""
    services = {
        "agent": {
            "name": "Agents (DSH)",
            "port": DSH_PORT,
            "up": _check_port_open(DSH_HOST, DSH_PORT),
        },
        "terminal": {
            "name": "Terminal (Ghostty)",
            "port": TERMINAL_PORT,
            "up": _check_port_open(TERMINAL_HOST, TERMINAL_PORT),
        },
        "jupyter": {
            "name": "JupyterLab",
            "port": JUPYTER_PORT,
            "up": _check_port_open(JUPYTER_HOST, JUPYTER_PORT),
        },
        "marimo": {
            "name": "Marimo",
            "port": MARIMO_PORT,
            "up": _check_port_open(MARIMO_HOST, MARIMO_PORT),
        },
        "vscode": {
            "name": "VS Code",
            "port": VSCODE_PORT,
            "up": _check_port_open(VSCODE_HOST, VSCODE_PORT),
        },
        "hub": {
            "name": "Compute & Hub",
            "port": WIZARD_PORT,
            "up": _check_port_open(WIZARD_HOST, WIZARD_PORT),
        },
    }
    scratch_dir = os.environ.get("SCRATCH", "/scratch")
    scratch_free_gb = 0.0
    if os.path.isdir(scratch_dir):
        with contextlib.suppress(OSError):
            usage = shutil.disk_usage(scratch_dir)
            scratch_free_gb = round(usage.free / (1024**3), 1)

    cpu_count = os.cpu_count() or 1
    load_avg = [round(x, 2) for x in os.getloadavg()] if hasattr(os, "getloadavg") else []

    workdir = _studio_workdir()
    return {
        "status": "ready" if any(s["up"] for s in services.values()) else "starting",
        "session_id": SESSION_ID or None,
        "prefix": PREFIX or None,
        "workdir": workdir,
        "workdir_ephemeral": bool(workdir) and _is_ephemeral(workdir),
        "home": os.path.expanduser("~"),
        "services": services,
        "resources": {
            "cpus": cpu_count,
            "load_avg": load_avg,
            "scratch_free_gb": scratch_free_gb,
        },
    }


def _forward(
    handler: BaseHTTPRequestHandler,
    host: str,
    port: int,
    path: str,
    *,
    body_filter: Callable[[bytes, str], bytes] | None = rewrite_body,
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
        service_name = "Service"
        if host == TERMINAL_HOST and port == TERMINAL_PORT:
            service_name = "Terminal"
        elif host == JUPYTER_HOST and port == JUPYTER_PORT:
            service_name = "JupyterLab"
        elif host == MARIMO_HOST and port == MARIMO_PORT:
            service_name = "Marimo"
        elif host == VSCODE_HOST and port == VSCODE_PORT:
            service_name = "VS Code"
        elif host == WIZARD_HOST and port == WIZARD_PORT:
            service_name = "Compute Hub"

        if host == DSH_HOST and port == DSH_PORT and _is_index_path(path):
            _send_html(handler, 200, STARTING_HTML)
            handler.log_message('"starting" %s (dsh not ready: %s)', path, exc)
            return

        fallback = (
            f"<!DOCTYPE html><html><body style='font-family:system-ui,-apple-system,sans-serif;padding:2rem;background:#181926;color:#cad3f5'>"
            f"<h1>{service_name} unavailable</h1>"
            f"<p>{service_name} is currently starting or not running on port {port}.</p>"
            f"<p><a href='{PREFIX or '/'}' style='color:#89b4fa'>← Return to Studio</a></p>"
            "</body></html>"
        ).encode()
        _send_html(handler, 503, fallback)
        return

    content_type = upstream.getheader("Content-Type") or ""
    raw = b"" if streaming else upstream.read()

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

    if not streaming and body_filter is not None:
        raw = body_filter(raw, content_type)

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
            if (
                _is_index_path(self.path)
                and not has_dsh_auth_cookie(self.headers.get("Cookie"))
                and not read_launch_token()
            ):
                _send_html(self, 200, STARTING_HTML)
                self.log_message('"starting" %s (waiting for dsh token)', self.path)
                return

        public = upstream_path(self.path)
        route = urlparse(public).path or "/"

        # API Status Endpoint
        if route == "/api/studio/status":
            _send_json(self, 200, get_studio_status())
            return

        # Trailing slash redirects for subservices
        for bare in ("/terminal", "/jupyter", "/marimo", "/vscode", "/hub"):
            if route == bare:
                target = f"{PREFIX}{bare}/" if PREFIX else f"{bare}/"
                qs = urlparse(public).query
                if qs:
                    target = f"{target}?{qs}"
                self.send_response(302, "Found")
                self.send_header("Location", target)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

        # Terminal (Ghostty-web)
        for term_prefix in ("/terminal", TERMINAL_MOUNT):
            if route == term_prefix or route.startswith(term_prefix + "/"):
                rest = route[len(term_prefix) :] or "/"
                qs = urlparse(public).query
                if qs:
                    rest = f"{rest}?{qs}" if "?" not in rest else f"{rest}&{qs}"
                _forward(self, TERMINAL_HOST, TERMINAL_PORT, rest, body_filter=None)
                return

        # JupyterLab / Marimo / VS Code serve under the full session base path
        # (startup-studio.sh passes /session/contrib/<id>/<tool>), so re-add PREFIX.
        prefixed = f"{PREFIX}{public}"

        # JupyterLab
        if route == "/jupyter" or route.startswith("/jupyter/"):
            _forward(self, JUPYTER_HOST, JUPYTER_PORT, prefixed, body_filter=inject_dock)
            return

        # Marimo
        if route == "/marimo" or route.startswith("/marimo/"):
            _forward(self, MARIMO_HOST, MARIMO_PORT, prefixed, body_filter=inject_dock)
            return

        # VS Code (OpenVSCode Server)
        if route == "/vscode" or route.startswith("/vscode/"):
            _forward(self, VSCODE_HOST, VSCODE_PORT, prefixed, body_filter=inject_dock)
            return

        # Compute & Agent Wizard Hub
        for hub_prefix in ("/hub", WIZARD_MOUNT):
            if route == hub_prefix or route.startswith(hub_prefix + "/"):
                rest = route[len(hub_prefix) :] or "/"
                qs = urlparse(public).query
                if qs:
                    rest = f"{rest}?{qs}" if "?" not in rest else f"{rest}&{qs}"
                _forward(self, WIZARD_HOST, WIZARD_PORT, rest, body_filter=None)
                return

        # Default: Forward to DSH
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
        f"studio-proxy: listening 0.0.0.0:{PUBLIC_PORT} → dsh={DSH_HOST}:{DSH_PORT} "
        f"terminal={TERMINAL_HOST}:{TERMINAL_PORT} jupyter={JUPYTER_HOST}:{JUPYTER_PORT} "
        f"marimo={MARIMO_HOST}:{MARIMO_PORT} vscode={VSCODE_HOST}:{VSCODE_PORT} "
        f"hub={WIZARD_HOST}:{WIZARD_PORT} prefix={PREFIX or '(none)'}\n"
    )
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
