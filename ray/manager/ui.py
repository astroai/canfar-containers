"""HTML helpers for the Ray manager contributed UI."""

from __future__ import annotations

import html
import os
from typing import Any
from urllib.parse import quote

RETRY_PHASES = frozenset({"CANFAR Failed", "Ray Unhealthy", "Orphaned"})


def public_prefix() -> str:
    """Browser-visible path prefix for CANFAR contributed sessions.

    Ingress serves the app at ``/session/contrib/<skaha_sessionid>/`` and strips
    that prefix before forwarding to the container (which still sees ``/``).
    Absolute links like ``/dashboard/`` therefore escape the session and hit
    ``https://workloads.canfar.net/dashboard/``. Prefix public URLs when the
    session id is present.
    """
    session_id = (os.environ.get("skaha_sessionid") or "").strip()
    if not session_id:
        return ""
    return f"/session/contrib/{session_id}"


def public_path(path: str = "/") -> str:
    """Map an app-absolute path (``/dashboard/``) to the browser-visible URL."""
    if not path.startswith("/"):
        path = "/" + path
    prefix = public_prefix()
    if path == "/":
        return f"{prefix}/" if prefix else "/"
    return f"{prefix}{path}"


def flash_html(flash: str | None, message: str | None) -> str:
    if not flash or not message:
        return ""
    safe = html.escape(message)
    kind = {
        "ok": "flash-ok",
        "error": "flash-error",
        "warn": "flash-warn",
    }.get(flash, "flash-info")
    return f'<p class="flash {kind}" role="alert">{safe}</p>'


def redirect_with_flash(path: str, flash: str, message: str) -> str:
    target = public_path(path)
    return f"{target}?flash={quote(flash)}&msg={quote(message)}"


def phase_class(phase: str) -> str:
    mapping = {
        "Running": "phase-ok",
        "Creating": "phase-busy",
        "Degraded": "phase-warn",
        "Failed": "phase-bad",
        "Stopped": "phase-muted",
        "Idle": "phase-muted",
        "Stopping": "phase-busy",
        "CANFAR Failed": "phase-bad",
        "Ray Unhealthy": "phase-warn",
        "Orphaned": "phase-warn",
    }
    return mapping.get(phase, "phase-muted")


def setup_ready(*, authenticated: bool, preflight: dict[str, Any] | None) -> bool:
    pf = preflight or {}
    return authenticated and bool(pf.get("passed"))


def setup_checklist_html(
    *,
    authenticated: bool,
    auth_idp: str | None,
    preflight: dict[str, Any] | None,
    preflight_action: str,
    ready: bool,
) -> str:
    pf = preflight or {}
    pf_passed = bool(pf.get("passed"))
    auth_item = "checklist-done" if authenticated else "checklist-todo"
    pf_item = "checklist-done" if pf_passed else "checklist-todo"
    auth_detail = (
        f'<span class="phase-ok">Authenticated ({html.escape(auth_idp or "ok")})</span>'
        if authenticated
        else (
            '<span class="phase-bad">Not authenticated</span>'
            ' — run in an AstroAI <strong>terminal</strong> or <strong>vscode</strong> session:'
            ' <code>canfar auth login</code>'
            ' <button type="button" class="btn btn-ghost btn-sm" id="copy-auth-cmd">Copy</button>'
        )
    )
    pf_detail = (
        f'<span class="phase-ok">Passed</span> (probe {html.escape(str(pf.get("worker_ip", "?")))})'
        if pf_passed
        else (
            '<span class="phase-warn">Not run or failed</span>'
            ' — verifies pod-to-pod networking before workers launch.'
        )
    )
    pf_action = ""
    if not pf_passed:
        pf_action = (
            f'<form class="inline checklist-action" method="post" action="{html.escape(preflight_action)}" '
            f'onsubmit="var b=this.querySelector(\'button[type=submit]\'); b.disabled=true; b.textContent=\'Running...\';">'
            f'<button class="btn btn-sm" type="submit">Run network preflight</button></form>'
        )
    ready_banner = (
        '<p class="checklist-ready" id="setup-ready-banner">Setup complete — you can create a cluster.</p>'
        if ready
        else (
            '<p class="checklist-blocked" id="setup-ready-banner">'
            "Complete the checklist above before creating a cluster.</p>"
        )
    )
    return f"""
    <ol class="checklist" id="setup-checklist">
      <li class="{auth_item}" id="checklist-auth">
        <span class="checklist-title">CANFAR authentication</span>
        <div class="checklist-body" id="auth-status">{auth_detail}</div>
      </li>
      <li class="{pf_item}" id="checklist-preflight">
        <span class="checklist-title">Network preflight</span>
        <div class="checklist-body" id="preflight-status">{pf_detail} {pf_action}</div>
      </li>
    </ol>
    {ready_banner}
    """


def workers_table_html(entries: list[dict[str, Any]], *, retry_action_prefix: str, logs_href_prefix: str) -> str:
    if not entries:
        return ""
    rows = []
    for entry in entries:
        sid = html.escape(entry["session_id"])
        name = html.escape(entry["name"])
        phase = html.escape(entry["phase"])
        phase_cls = html.escape(entry["phase_class"])
        canfar = html.escape(entry["canfar_status"])
        ip = html.escape(entry["worker_ip"])
        joined_label = html.escape(entry["joined_label"])
        last_error = html.escape(entry["last_error"])
        notes = last_error
        if entry.get("logs_available"):
            logs_href = html.escape(f"{logs_href_prefix}{entry['session_id']}/logs")
            notes += (
                f' <a href="{logs_href}" target="_blank" rel="noopener noreferrer" '
                f'aria-label="View logs for worker {name}">logs</a>'
            )
        if entry.get("retry_available"):
            retry_action = html.escape(f"{retry_action_prefix}{entry['session_id']}")
            notes += (
                f' <form class="inline" method="post" action="{retry_action}" '
                f'onsubmit="var b=this.querySelector(\'button[type=submit]\'); b.disabled=true; b.textContent=\'Retrying...\';">'
                f'<button class="btn btn-ghost" type="submit" aria-label="Retry worker {name}">Retry</button></form>'
            )
        rows.append(
            f"<tr><td>{name}</td><td><code>{sid}</code></td>"
            f'<td class="{phase_cls}">{phase}</td>'
            f"<td>{canfar}</td>"
            f'<td class="mono">{ip}</td>'
            f"<td>{joined_label}</td>"
            f"<td>{notes}</td></tr>"
        )
    return (
        '<table id="workers-table"><tr><th>Name</th><th>Session</th><th>Phase</th><th>CANFAR</th>'
        "<th>IP</th><th>Ray</th><th>Notes</th></tr>"
        + "".join(rows)
        + "</table>"
    )


PAGE_STYLE = """
:root {
  --bg: #0f1419;
  --bg-elevated: #1a222c;
  --bg-card: #1e2733;
  --border: #2d3a4a;
  --text: #e7eef7;
  --muted: #9aabbd;
  --accent: #3d9cf0;
  --accent-hover: #5aaff5;
  --ok: #3dd68c;
  --warn: #f5c542;
  --bad: #f07178;
  --info: #7aa2f7;
  --radius: 10px;
  --font: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
  --mono: "IBM Plex Mono", "SF Mono", ui-monospace, monospace;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: var(--font);
  background:
    radial-gradient(1200px 600px at 10% -10%, rgba(61,156,240,0.18), transparent 55%),
    radial-gradient(900px 500px at 100% 0%, rgba(61,214,140,0.08), transparent 50%),
    var(--bg);
  color: var(--text);
  line-height: 1.45;
  min-height: 100vh;
}
a { color: var(--accent); text-decoration: none; }
a:hover { color: var(--accent-hover); text-decoration: underline; }
.wrap { max-width: 1100px; margin: 0 auto; padding: 1.25rem 1.25rem 3rem; }
.topbar {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
  gap: 1rem; margin-bottom: 1.25rem;
}
.brand h1 { margin: 0; font-size: 1.45rem; font-weight: 650; letter-spacing: -0.02em; }
.brand p { margin: 0.2rem 0 0; color: var(--muted); font-size: 0.92rem; }
.cta-row { display: flex; flex-wrap: wrap; gap: 0.6rem; align-items: center; }
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 0.4rem;
  border: 1px solid var(--border); background: var(--bg-card); color: var(--text);
  border-radius: 8px; padding: 0.5rem 0.9rem; font: inherit; font-weight: 560;
  cursor: pointer; text-decoration: none;
}
.btn:hover { border-color: var(--accent); color: var(--text); text-decoration: none; }
.btn-primary {
  background: linear-gradient(180deg, #4aa6f5, #2f86d6);
  border-color: #2a78c4; color: #fff;
}
.btn-primary:hover { filter: brightness(1.06); color: #fff; }
.btn-danger { border-color: #8a3a40; color: #ffb4b8; }
.btn-ghost { background: transparent; }
.cards {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 0.75rem; margin: 1rem 0 1.25rem;
}
.card {
  background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 0.85rem 1rem;
}
.card .label { color: var(--muted); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; }
.card .value { margin-top: 0.35rem; font-size: 1.15rem; font-weight: 650; }
.card .sub { margin-top: 0.2rem; color: var(--muted); font-size: 0.82rem; font-family: var(--mono); }
.panel {
  background: var(--bg-elevated); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 1rem 1.1rem; margin-bottom: 1rem;
}
.panel h2 { margin: 0 0 0.75rem; font-size: 1.05rem; }
.grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 0.65rem; margin: 0.5rem 0 1rem;
}
label { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.85rem; color: var(--muted); }
input, select {
  background: var(--bg); border: 1px solid var(--border); border-radius: 7px;
  color: var(--text); padding: 0.45rem 0.55rem; font: inherit;
}
table { border-collapse: collapse; width: 100%; margin: 0.35rem 0 0.25rem; font-size: 0.9rem; }
th, td { border-bottom: 1px solid var(--border); padding: 0.55rem 0.45rem; text-align: left; vertical-align: top; }
th { color: var(--muted); font-weight: 560; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em; }
code, .mono { font-family: var(--mono); font-size: 0.86em; }
.actions { display: flex; flex-wrap: wrap; gap: 0.5rem; }
form.inline { display: inline; margin: 0; }
.flash { padding: 0.7rem 0.9rem; border-radius: 8px; margin: 0 0 1rem; border: 1px solid transparent; }
.flash-ok { background: rgba(61,214,140,0.12); border-color: rgba(61,214,140,0.35); color: var(--ok); }
.flash-error { background: rgba(240,113,120,0.12); border-color: rgba(240,113,120,0.35); color: var(--bad); }
.flash-warn { background: rgba(245,197,66,0.12); border-color: rgba(245,197,66,0.35); color: var(--warn); }
.flash-info { background: rgba(122,162,247,0.12); border-color: rgba(122,162,247,0.35); color: var(--info); }
.phase-ok { color: var(--ok); }
.phase-busy { color: var(--accent); }
.phase-warn { color: var(--warn); }
.phase-bad { color: var(--bad); }
.phase-muted { color: var(--muted); }
.pill {
  display: inline-block; padding: 0.12rem 0.5rem; border-radius: 999px;
  border: 1px solid var(--border); font-size: 0.78rem; font-weight: 600;
}
.muted { color: var(--muted); }
.footer { margin-top: 1.5rem; color: var(--muted); font-size: 0.85rem; }
.progress {
  height: 8px; background: var(--bg); border-radius: 999px; overflow: hidden; margin-top: 0.55rem;
}
.progress > span {
  display: block; height: 100%; background: linear-gradient(90deg, #2f86d6, #3dd68c);
  width: 0%; transition: width 0.4s ease;
}
.op-banner {
  display: none; margin-bottom: 1rem; padding: 0.7rem 0.9rem; border-radius: 8px;
  border: 1px solid rgba(61,156,240,0.35); background: rgba(61,156,240,0.1);
}
.op-banner.active { display: block; }
.checklist {
  list-style: none; margin: 0 0 0.75rem; padding: 0;
  display: flex; flex-direction: column; gap: 0.65rem;
}
.checklist li {
  border: 1px solid var(--border); border-radius: 8px; padding: 0.7rem 0.85rem;
  background: var(--bg);
}
.checklist-done { border-color: rgba(61,214,140,0.35); }
.checklist-todo { border-color: rgba(245,197,66,0.35); }
.checklist-title { display: block; font-weight: 600; margin-bottom: 0.25rem; }
.checklist-body { color: var(--muted); font-size: 0.9rem; }
.checklist-action { margin-left: 0.35rem; }
.checklist-ready { color: var(--ok); margin: 0 0 0.5rem; }
.checklist-blocked { color: var(--warn); margin: 0 0 0.5rem; }
.btn-sm { padding: 0.3rem 0.65rem; font-size: 0.82rem; }
fieldset[disabled] { opacity: 0.55; pointer-events: none; }
fieldset[disabled] .create-hint { opacity: 1; pointer-events: auto; }
"""
