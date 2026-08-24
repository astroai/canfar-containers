"""AstroAI hub sidecar — batch compute + agent table.

Listens on 127.0.0.1:ASTROAI_AGENT_WIZARD_PORT (default 4792).
Proxied as /astroai-agents/ by the session path-rewrite proxy.
Failures here must never affect the main UI process.

Surface:
  1. Status — CANFAR auth, ray-manager, OpenResearch wire
  2. Start batch compute — autoscaling manager + wire
  3. Agent table — same columns as `astroai agent list`, plus Install / Setup
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlparse

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from session_title import session_tab_title  # noqa: E402

PORT = int(os.environ.get("ASTROAI_AGENT_WIZARD_PORT", "4792"))
CLI_TIMEOUT = int(os.environ.get("ASTROAI_AGENT_WIZARD_CLI_TIMEOUT", "600"))
PLATFORM_CANFAR_TIMEOUT = int(os.environ.get("ASTROAI_HUB_CANFAR_TIMEOUT", "12"))
COMPUTE_ENSURE_TIMEOUT = int(os.environ.get("ASTROAI_HUB_COMPUTE_ENSURE_TIMEOUT", "1200"))
RAY_MANAGER_IMAGE = os.environ.get(
    "RAY_MANAGER_IMAGE", "images.canfar.net/astroai/ray-manager:latest"
)
HOME = Path.home()
SESSION_KIND = (os.environ.get("ASTROAI_SESSION_KIND") or "").strip().lower()
BACK_UI_LABEL = {
    "openresearch": "OpenResearch",
}.get(SESSION_KIND, "main UI")
WIRE_OPENRESEARCH = SESSION_KIND == "openresearch"


def _run_cmd(cmd: list[str], *, timeout: int) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", f"{cmd[0]} not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    except OSError as exc:
        return 1, "", str(exc)


def _run_lab(args: list[str], *, timeout: int | None = None) -> tuple[int, str, str]:
    lab = shutil.which("astroai") or "/opt/astroai/venv/cadc/bin/astroai"
    return _run_cmd([lab, *args], timeout=timeout or CLI_TIMEOUT)


def _parse_json_stdout(stdout: str) -> object | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            with contextlib.suppress(json.JSONDecodeError):
                return json.loads(text[start : end + 1])
        start_l = text.find("[")
        end_l = text.rfind("]")
        if start_l >= 0 and end_l > start_l:
            with contextlib.suppress(json.JSONDecodeError):
                return json.loads(text[start_l : end_l + 1])
        return None


def _load_wire() -> ModuleType:
    """Load sibling orx-wire-compute.py (hyphenated filename)."""
    path = Path(__file__).resolve().parent / "orx-wire-compute.py"
    spec = importlib.util.spec_from_file_location("orx_wire_compute", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _log_tail(n: int = 40) -> str:
    path = HOME / ".astroai" / "lab" / "agent-setup.log"
    if not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


def _canfar_auth_line() -> tuple[bool, str]:
    if shutil.which("canfar") is None:
        return False, "canfar CLI not on PATH"
    rc, out, err = _run_cmd(["canfar", "auth", "show"], timeout=PLATFORM_CANFAR_TIMEOUT)
    line = ((out or err or "").strip().splitlines() or [""])[0].strip()
    if rc == 124:
        return False, f"canfar auth show timed out after {PLATFORM_CANFAR_TIMEOUT}s"
    if rc != 0 and not line:
        return False, "Not authenticated — run canfar login in webterm"
    bad = not line or any(
        tok in line.lower() for tok in ("not authenticated", "timed out", "failed", "error")
    )
    return (not bad), (line or "unknown")


def _orx_wire_state(wire: ModuleType) -> dict[str, Any]:
    """Read OpenResearch ray.json / settings.json when present."""
    cfg = wire._orx_config_dir()
    address = ""
    backend = ""
    ray_path = cfg / "ray.json"
    settings_path = cfg / "settings.json"
    if ray_path.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            data = json.loads(ray_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                address = str(data.get("address") or "").rstrip("/")
    if settings_path.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                backend = str(data.get("defaultBackend") or "")
    return {"address": address, "default_backend": backend, "wired": bool(address)}


def _ray_status() -> dict[str, Any]:
    """Manager + Jobs URL + optional OpenResearch wire (JSON-backed)."""
    try:
        wire = _load_wire()
    except Exception as exc:  # noqa: BLE001
        return {
            "manager_running": False,
            "manager_pending": False,
            "compute_ready": False,
            "hint": f"wire helpers unavailable: {exc}",
        }

    managers = wire.find_manager_sessions()
    running = [m for m in managers if wire._session_status(m) == "Running"]
    pending = [m for m in managers if wire._session_status(m) == "Pending"]
    connect = wire._session_connect_url(running[0]) if running else ""
    jobs = (os.environ.get("ASTROAI_RAY_JOBS_ADDRESS") or "").strip().rstrip("/")
    if not jobs and connect:
        jobs = wire.jobs_url_from_connect(connect).rstrip("/")

    orx = _orx_wire_state(wire) if WIRE_OPENRESEARCH else {"wired": False, "address": "", "default_backend": ""}
    if WIRE_OPENRESEARCH and orx["address"] and not jobs:
        jobs = orx["address"]
    wired = bool(WIRE_OPENRESEARCH and orx["wired"] and jobs)

    if running and wired:
        hint = "Batch compute ready — go back and run experiments."
    elif running and WIRE_OPENRESEARCH and not wired:
        hint = "Manager is Running — click Start batch compute to wire OpenResearch."
    elif running:
        hint = "Manager is Running."
    elif pending:
        hint = "Manager session is Pending — click Start batch compute to wait and finish."
    else:
        hint = "No ray-manager yet — click Start batch compute."

    return {
        "manager_running": bool(running),
        "manager_pending": bool(pending) and not running,
        "connect_url": connect or None,
        "ray_address": jobs or None,
        "orx_wired": wired,
        "orx_default_backend": orx.get("default_backend") or None,
        "wire_supported": WIRE_OPENRESEARCH,
        "compute_ready": bool(running) and (wired if WIRE_OPENRESEARCH else True),
        "hint": hint,
    }


def _platform_payload() -> dict[str, Any]:
    auth_ok, auth_line = _canfar_auth_line()
    ray = _ray_status()
    return {
        "ok": bool(ray.get("manager_running")),
        "session_kind": SESSION_KIND,
        "image_tag": os.environ.get("RAY_IMAGE_TAG")
        or os.environ.get("BUILD_TAG")
        or "latest",
        "canfar": {
            "available": shutil.which("canfar") is not None,
            "auth_ok": auth_ok,
            "auth": auth_line,
            "sessions": [],  # lean UI: no raw ps dump
        },
        "ray": ray,
    }


def _agent_report() -> tuple[int, dict[str, Any]]:
    """Full `agent list --json` payload (same shape as /api/report)."""
    rc, out, err = _run_lab(["--json", "agent", "list"], timeout=120)
    data = _parse_json_stdout(out)
    if isinstance(data, dict):
        data.setdefault("log_tail", _log_tail())
        data["cli_exit"] = rc
        return (200 if rc in (0, 1) else 500), data
    return 500, {
        "ok": False,
        "error": err or out or "agent list failed",
        "cli_exit": rc,
        "agents": [],
        "issues": [],
    }


def _safe_agent_id(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip()
    if not s or len(s) > 64:
        return None
    if not all(c.isalnum() or c in "._-" for c in s):
        return None
    return s


def _setup_payload(agent: str) -> dict[str, Any]:
    """`astroai agent setup <id>` — config/skills/default plugins, not the CLI."""
    rc, out, err = _run_lab(["--yes", "--json", "agent", "setup", agent])
    data = _parse_json_stdout(out) or {}
    if not isinstance(data, dict):
        data = {}
    data["ok"] = rc == 0
    data["partial"] = rc == 2
    data["cli_exit"] = rc
    if rc == 0:
        actions = data.get("actions") or []
        detail = ""
        if isinstance(actions, list) and actions:
            detail = " · " + "; ".join(str(a) for a in actions[:4])
        data["summary"] = f"setup {agent} ok"
        data["user_message"] = f"setup {agent} ok{detail}"
    elif rc == 2:
        data["summary"] = f"partial setup {agent}"
        data["user_message"] = data["summary"]
    else:
        msg = (err or out or "setup failed")[:300]
        data["summary"] = msg
        data["user_message"] = msg
    return data


def _create_manager_if_needed(wire: ModuleType) -> tuple[bool, str, list[str]]:
    """Idempotent ray-manager session create. Returns (ok, detail, steps)."""
    steps: list[str] = []
    managers = wire.find_manager_sessions()
    if any(wire._session_status(m) in {"Running", "Pending"} for m in managers):
        steps.append("manager-exists")
        return True, "ray-manager session already present", steps

    rc, out, err = _run_cmd(
        [
            "canfar",
            "create",
            "--name",
            "raymgr",
            "--cpu",
            "2",
            "--memory",
            "8",
            "contributed",
            RAY_MANAGER_IMAGE,
        ],
        timeout=COMPUTE_ENSURE_TIMEOUT,
    )
    text = f"{err or ''}\n{out or ''}".lower()
    if rc == 0:
        _step("create")
        return True, "ray-manager session created", steps
    # Name collision / already exists → treat as ok and continue ensure.
    if any(tok in text for tok in ("already", "conflict", "exists", "duplicate")):
        _step("create-exists")
        return True, "ray-manager name already exists — continuing", steps
    return False, (err or out or "canfar create failed")[:800], steps


def _write_autoscaling_env() -> str:
    """Skaha will not pass -e into a contributed manager; this file is sourced at start."""
    path = Path.home() / ".config" / "canfar" / "lab" / "ray-manager.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "RAY_AUTOSCALING_ENABLED=1\n"
        "RAY_AUTOSCALING_MIN_WORKERS=0\n"
        "RAY_AUTOSCALING_MAX_WORKERS=8\n"
        "RAY_AUTOSCALING_CORES=1\n"
        "RAY_AUTOSCALING_RAM_GB=4\n"
        "RAY_AUTOSCALING_GPUS=0\n"
        "RAY_AUTOSCALING_IDLE_TIMEOUT_MINUTES=5\n",
        encoding="utf-8",
    )
    return str(path)


# ---------------------------------------------------------------------------
# Background compute-ensure job (the hub button must never block a fetch)
# ---------------------------------------------------------------------------

_ENSURE_LOCK = threading.Lock()
_ENSURE_STATE: dict[str, Any] = {
    "running": False,
    "steps": [],
    "result": None,
    "started": 0.0,
    "finished": 0.0,
}


def _ensure_note(step: str) -> None:
    """Record one completed ensure milestone for /api/compute/status."""
    with _ENSURE_LOCK:
        _ENSURE_STATE["steps"] = list(dict.fromkeys([*_ENSURE_STATE["steps"], step]))


def _start_compute_ensure() -> dict[str, Any]:
    """Run ``_compute_ensure`` in a daemon thread; return immediately.

    A synchronous run holds the HTTP request for many minutes while Harbor
    pulls the manager image, which made the hub button look hung. The page
    polls ``/api/compute/status`` instead.
    """
    with _ENSURE_LOCK:
        if _ENSURE_STATE["running"]:
            return {"ok": True, "running": True, "summary": "already starting"}
        _ENSURE_STATE.update(
            running=True, steps=[], result=None, started=time.time(), finished=0.0
        )

    def _worker() -> None:
        try:
            result = _compute_ensure()
        except Exception as exc:  # noqa: BLE001 — surface to the poller
            result = {
                "ok": False,
                "summary": f"ensure crashed: {exc}",
                "user_message": str(exc)[:300],
                "error": str(exc)[:500],
                "steps": [],
            }
        with _ENSURE_LOCK:
            _ENSURE_STATE["running"] = False
            _ENSURE_STATE["result"] = result
            _ENSURE_STATE["finished"] = time.time()

    threading.Thread(target=_worker, daemon=True, name="compute-ensure").start()
    return {"ok": True, "started": True, "running": True, "summary": "batch compute starting"}


def _compute_status() -> dict[str, Any]:
    """Snapshot of the ensure job for the polling UI."""
    with _ENSURE_LOCK:
        running = _ENSURE_STATE["running"]
        steps = list(_ENSURE_STATE["steps"])
        started = _ENSURE_STATE["started"]
        result = _ENSURE_STATE["result"]
    if running:
        return {
            "ok": True,
            "running": True,
            "steps": steps,
            "elapsed": round(max(0.0, time.time() - started)) if started else 0,
            "summary": "Starting batch compute…",
            "user_message": f"Starting batch compute… ({', '.join(steps) or 'preparing'})",
        }
    if isinstance(result, dict):
        out = dict(result)
        out["running"] = False
        return out
    return {"ok": True, "running": False, "steps": [], "summary": "idle"}


def _compute_ensure() -> dict[str, Any]:
    """Ensure an autoscaling ray-manager, then wire OpenResearch when applicable."""
    try:
        wire = _load_wire()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "summary": f"wire helpers unavailable: {exc}", "steps": []}

    def _step(name: str) -> None:
        steps.append(name)
        _ensure_note(name)

    steps: list[str] = []
    _write_autoscaling_env()
    _step("autoscaling-env")
    ok, detail, created = _create_manager_if_needed(wire)
    for name in created:
        if name not in steps:
            _step(name)
    if not ok:
        return {
            "ok": False,
            "summary": "could not create ray-manager",
            "user_message": detail,
            "error": detail,
            "steps": steps,
        }

    jobs = ""
    workers: dict[str, Any] = {}
    connect = ""
    if shutil.which("astroai"):
        ensure_rc, ensure_out, ensure_err = _run_cmd(
            [
                "astroai",
                "cluster",
                "start",
                "--json",
                "--timeout",
                str(COMPUTE_ENSURE_TIMEOUT),
            ],
            timeout=COMPUTE_ENSURE_TIMEOUT,
        )
        _step("cluster-start")
        payload = _parse_json_stdout(ensure_out) if ensure_rc == 0 else None
        if isinstance(payload, dict):
            jobs = str(payload.get("jobs_address") or "").rstrip("/")
            connect = str(payload.get("manager_url") or payload.get("connect_url") or "")
            workers = {
                "joined_workers": payload.get("joined_workers"),
                "cluster_phase": payload.get("cluster_phase"),
            }
        elif ensure_rc != 0:
            return {
                "ok": False,
                "summary": "manager present but cluster start failed",
                "user_message": (
                    f"astroai cluster start failed: "
                    f"{(ensure_err or ensure_out or 'unknown')[:600]}"
                ),
                "error": (ensure_err or ensure_out or "")[:800],
                "steps": steps,
            }

    if not jobs:
        managers = wire.find_manager_sessions()
        running = [
            m
            for m in managers
            if wire._session_status(m) == "Running" and wire._session_connect_url(m)
        ]
        if running:
            connect = wire._session_connect_url(running[0])
            jobs = wire.jobs_url_from_connect(connect).rstrip("/")
            _step("discover-jobs")

    wired = None
    if WIRE_OPENRESEARCH and jobs:
        try:
            wired = wire.wire_orx(jobs_address=jobs, make_default=True)
            _step("wire-orx")
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "summary": "cluster up but OpenResearch wire failed",
                "user_message": str(exc)[:600],
                "jobs_address": jobs,
                "connect_url": connect or None,
                "steps": steps,
                "error": str(exc),
            }

    ready = bool(jobs) and (not WIRE_OPENRESEARCH or bool(wired))
    if ready:
        msg = "Batch compute ready. Jobs with --cpus will add workers."
        if WIRE_OPENRESEARCH:
            msg += " OpenResearch is wired — go back and run."
        elif connect:
            msg += f" Manager: {connect}"
        if "manager-exists" in steps:
            msg += " Stop the manager and click again if jobs do not add workers."
    elif jobs:
        msg = f"Jobs URL: {jobs} (wire skipped for this session kind)."
    else:
        msg = detail + " — waiting for manager connect URL; click again when Running."

    return {
        "ok": ready or bool(jobs),
        "summary": "batch compute ready" if ready else "partial",
        "user_message": msg,
        "jobs_address": jobs or None,
        "connect_url": connect or None,
        "workers": workers,
        "wired": wired,
        "steps": steps,
    }


# ---------------------------------------------------------------------------
# Legacy helpers kept for smoke/unit tests (not shown in the lean UI)
# ---------------------------------------------------------------------------


def _plugins_from_list_config(tag: str | None = None) -> tuple[int, list[dict], str]:
    rc, out, err = _run_lab(["--json", "agent", "plugins", "list"], timeout=60)
    data = _parse_json_stdout(out)
    rows = data if isinstance(data, list) else []
    if tag:
        rows = [r for r in rows if isinstance(r, dict) and tag in (r.get("tags") or [])]
    for row in rows:
        if isinstance(row, dict) and "installed" not in row:
            row["installed"] = bool(row.get("any_installed"))
    return rc, rows, err or out or ""


def _catalog_items() -> tuple[int, list[dict], str]:
    rc_a, out_a, err_a = _run_lab(["--json", "agent", "list"], timeout=60)
    agents = _parse_json_stdout(out_a)
    items: list[dict] = []
    if isinstance(agents, dict):
        for a in agents.get("agents") or []:
            if not isinstance(a, dict):
                continue
            aid = a.get("id") or a.get("agent") or "?"
            items.append(
                {
                    "id": aid,
                    "kind": "agent",
                    "installed": bool(a.get("binary") or a.get("binary_ok")),
                    "summary": a.get("summary") or "",
                }
            )
    rc_p, plugins, err_p = _plugins_from_list_config()
    for p in plugins:
        items.append(
            {
                "id": p.get("id"),
                "kind": p.get("kind") or "plugin",
                "installed": bool(p.get("installed") or p.get("any_installed")),
                "summary": p.get("summary") or "",
            }
        )
    rc = 0 if rc_a in (0, 1) and rc_p in (0, 1) else max(rc_a, rc_p)
    err = ""
    if rc_a not in (0, 1):
        err = err_a or out_a
    elif rc_p not in (0, 1):
        err = err_p
    return rc, items, err


def _install_plugins_by_tag(tag: str) -> tuple[int, dict]:
    rc_list, rows, err_list = _plugins_from_list_config(tag)
    if rc_list not in (0, 1):
        return rc_list, {
            "ok": False,
            "actions": [],
            "summary": err_list or "plugins list failed",
            "partial": False,
        }
    actions: list[dict] = []
    worst = 0
    for row in rows:
        pid = str(row.get("id") or "")
        if not pid:
            continue
        rc, out, err = _run_lab(["--yes", "--json", "agent", "plugins", "install", pid])
        data = _parse_json_stdout(out)
        if isinstance(data, dict) and isinstance(data.get("actions"), list):
            for a in data["actions"]:
                if isinstance(a, dict):
                    actions.append(a)
                else:
                    actions.append({"id": pid, "status": "ok", "detail": str(a)})
            if not data.get("ok", rc == 0):
                worst = max(worst, rc or 1)
        elif rc == 0:
            actions.append({"id": pid, "status": "ok", "detail": ""})
        else:
            worst = max(worst, rc or 1)
            actions.append(
                {"id": pid, "status": "failed", "detail": (err or out or "failed")[:200]}
            )
    n_ok = sum(1 for a in actions if a.get("status") not in ("failed", "skipped"))
    n_skip = sum(1 for a in actions if a.get("status") == "skipped")
    n_fail = sum(1 for a in actions if a.get("status") == "failed")
    return worst, {
        "ok": worst == 0,
        "partial": n_fail > 0 and n_ok > 0,
        "actions": actions,
        "cli_exit": worst,
        "summary": f"lean plugins: {n_ok} installed, {n_skip} skipped, {n_fail} failed",
    }


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>AstroAI</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Source+Sans+3:wght@400;600&family=Fraunces:opsz,wght@9..144,600;9..144,700&display=swap" rel="stylesheet"/>
<style>
  :root {
    --bg0: #07110e;
    --bg1: #0d1c16;
    --ink: #e7f2ea;
    --muted: #8a9e92;
    --line: #1e3228;
    --teal: #2bb8a8;
    --teal-ink: #03201c;
    --sky: #9ec9ff;
    --ok: #5dde9a;
    --warn: #e6b84d;
    --err: #ff6b7a;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    color: var(--ink);
    font-family: "Source Sans 3", "Segoe UI", sans-serif;
    background:
      radial-gradient(ellipse 80% 50% at 12% -10%, rgba(43,184,168,.22), transparent 55%),
      radial-gradient(ellipse 60% 40% at 88% 0%, rgba(158,201,255,.08), transparent 50%),
      linear-gradient(165deg, var(--bg1), var(--bg0));
    padding: clamp(1.25rem, 4vw, 3rem);
  }
  .wrap { max-width: 56rem; margin: 0 auto; }
  .back {
    display: inline-block; color: var(--sky); text-decoration: none;
    font-weight: 600; font-size: .95rem; margin-bottom: 1.5rem;
  }
  .back:hover { text-decoration: underline; }
  h1 {
    font-family: Fraunces, Georgia, serif;
    font-size: clamp(2.4rem, 7vw, 3.1rem);
    font-weight: 700; letter-spacing: -.03em;
    margin: 0 0 .4rem; line-height: 1.05;
  }
  .lede {
    color: var(--muted); margin: 0 0 1.75rem;
    font-size: 1.05rem; line-height: 1.45;
  }
  h2 {
    font-family: "Source Sans 3", sans-serif;
    font-size: 1.15rem; font-weight: 600;
    margin: 1.75rem 0 .35rem;
  }
  .lede-sm {
    color: var(--muted); margin: 0 0 .75rem;
    font-size: .95rem; line-height: 1.4;
  }
  .status {
    border-top: 1px solid var(--line);
    padding: 1rem 0 1.25rem;
    margin-bottom: .25rem;
  }
  .row {
    display: grid; grid-template-columns: 7.5rem 1fr;
    gap: .5rem 1rem; padding: .35rem 0;
    font-size: .98rem; align-items: baseline;
  }
  .row .k { color: var(--muted); font-size: .82rem; letter-spacing: .06em; text-transform: uppercase; }
  .ok { color: var(--ok); } .bad { color: var(--err); } .warn { color: var(--warn); }
  .actions { display: flex; flex-wrap: wrap; gap: .55rem; margin: 1rem 0 .75rem; }
  button {
    font: 600 .92rem/1 "Source Sans 3", sans-serif;
    border: 0; border-radius: 6px; padding: .7rem 1.1rem;
    cursor: pointer; color: var(--teal-ink); background: var(--teal);
  }
  button.secondary {
    background: transparent; color: var(--ink);
    border: 1px solid var(--line);
  }
  button:hover { filter: brightness(1.06); }
  button:disabled { opacity: .5; cursor: wait; filter: none; }
  #msg { min-height: 1.3rem; color: var(--muted); font-size: .95rem; margin-bottom: .5rem; white-space: pre-wrap; }
  #msg.ok { color: var(--ok); } #msg.warn { color: var(--warn); } #msg.bad { color: var(--err); }
  .foot {
    margin-top: 2rem; padding-top: 1rem; border-top: 1px solid var(--line);
    color: var(--muted); font-size: .88rem; line-height: 1.45;
  }
  code {
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-size: .84em; background: rgba(0,0,0,.28);
    border: 1px solid var(--line); border-radius: 4px; padding: .05rem .3rem;
  }
  a.mgr { color: var(--sky); }
  table.agent-list {
    width: 100%; border-collapse: collapse;
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-size: .84rem;
  }
  table.agent-list th {
    text-align: left; color: var(--muted);
    font-size: .72rem; letter-spacing: .06em;
    text-transform: uppercase; font-weight: 500;
    padding: .5rem .45rem; border-bottom: 1px solid var(--line);
  }
  table.agent-list td {
    padding: .42rem .45rem; border-bottom: 1px solid var(--line);
    vertical-align: middle; white-space: nowrap;
  }
  table.agent-list td.acts { text-align: right; }
  table.agent-list tr.on td:first-child { font-weight: 600; }
  table.agent-list .mark.ok { color: var(--ok); }
  table.agent-list .mark { color: var(--muted); }
  table.agent-list button.row-act {
    padding: .32rem .65rem; font-size: .78rem; margin-left: .3rem;
  }
  .stamp { color: var(--muted); font-size: .82rem; margin: .65rem 0 0; }
  .issues { color: var(--warn); font-size: .88rem; margin: .5rem 0 0; }
</style>
</head>
<body>
  <div class="wrap">
    <a class="back" id="back-link" href="../">← Back to __BACK_LABEL__</a>
    <h1>AstroAI</h1>
    <p class="lede">Start batch compute for this session. Agent CLIs and configs live on shared <code>/arc/home</code>.</p>

    <div class="status" id="status">Loading…</div>
    <div class="actions">
      <button id="btn-compute">Start batch compute</button>
    </div>
    <div id="msg"></div>

    <h2>Agents</h2>
    <p class="lede-sm">Same columns as <code>astroai agent list</code>. Install puts the CLI on PATH; Setup writes that agent's config, skills, and default plugins.</p>
    <div id="agent-table">Loading…</div>
    <p class="foot">
      Need <code>canfar login</code>? Open <strong>webterm</strong> (same home), then come back.<br/>
      Power users: <code>astroai agent …</code> · <code>astroai cluster …</code>
    </p>
  </div>
<script>
const BACK_LABEL = __BACK_LABEL_JSON__;
const base = (location.pathname.replace(/\\/?$/, '/') );
function mainUiHref() {
  // 1. The path we actually came from (set when the chip click lands here,
  //    and by document.referrer on first paint) — robust to any ingress
  //    shape, including root-mounted sessions where the marker heuristic
  //    wrongly resolves to the bare domain.
  try {
    const saved = sessionStorage.getItem('astroai-hub-back');
    if (saved) return saved;
  } catch (e) { /* storage unavailable — fall through */ }
  if (document.referrer && referrerOrigin() === location.origin
      && !document.referrer.includes('/astroai-' + 'agents')) {
    return document.referrer;
  }
  // 2. Marker heuristic for direct loads with a session prefix.
  const p = location.pathname;
  const marker = '/astroai-' + 'agents';
  const i = p.lastIndexOf(marker);
  if (i > 0) return p.slice(0, i) + '/';
  return '../';
}
(function initBack() {
  try {
    if (!sessionStorage.getItem('astroai-hub-back') && document.referrer
        && referrerOrigin() === location.origin) {
      const r = new URL(document.referrer);
      if (!r.pathname.includes('/astroai-' + 'agents')) {
        sessionStorage.setItem('astroai-hub-back', r.pathname + r.search);
      }
    }
  } catch (e) { /* ignore */ }
  const a = document.getElementById('back-link');
  a.href = mainUiHref();
  a.textContent = '← Back to ' + BACK_LABEL;
})();
function referrerOrigin() {
  try { return new URL(document.referrer).origin; } catch (e) { return ''; }
}
async function api(path, opts) {
  const r = await fetch(base.replace(/\\/?$/, '/') + path.replace(/^\\//,''), opts || {});
  const text = await r.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { ok: false, error: text }; }
  return { status: r.status, data };
}
function setMsg(t, cls) {
  const el = document.getElementById('msg');
  el.textContent = t || '';
  el.className = cls || '';
}
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function setBusy(on) {
  const compute = document.getElementById('btn-compute');
  if (compute) compute.disabled = !!on;
  document.querySelectorAll('button.row-act').forEach(b => { b.disabled = !!on; });
}
function whereLabel(row) {
  const binaryOk = !!(row.binary_ok || row.binary);
  if (!binaryOk) return '-';
  const srcRaw = row.binary_source || (row.managed ? 'managed' : '-');
  if (row.home_install && !row.managed) return 'home';
  if (srcRaw === 'managed') return 'scratch';
  if (srcRaw === 'other') return 'image';
  return srcRaw || '-';
}
function mark(ok) {
  return ok ? '<span class="mark ok">✓</span>' : '<span class="mark">-</span>';
}
function renderStatus(p) {
  const c = (p && p.canfar) || {};
  const r = (p && p.ray) || {};
  const authOk = !!c.auth_ok;
  const mgr = !!r.manager_running;
  const pending = !!r.manager_pending;
  let mgrLabel = '<span class="bad">none</span>';
  if (mgr) mgrLabel = '<span class="ok">Running</span>';
  else if (pending) mgrLabel = '<span class="warn">Pending</span>';

  let wireRow = '';
  if (r.wire_supported) {
    const wired = !!r.orx_wired;
    wireRow = `<div class="row"><span class="k">OpenResearch</span><span>${wired ? '<span class="ok">wired</span>' : '<span class="warn">not wired</span>'}</span></div>`;
  }

  let extra = '';
  if (r.connect_url && mgr) {
    extra += `<div class="row"><span class="k">Manager</span><span><a class="mgr" href="${esc(r.connect_url)}" target="_blank" rel="noopener">open panel</a></span></div>`;
  }
  if (r.ray_address) {
    extra += `<div class="row"><span class="k">Jobs URL</span><span><code>${esc(r.ray_address)}</code></span></div>`;
  }

  const hint = r.hint ? `<div class="row"><span class="k">Note</span><span>${esc(r.hint)}</span></div>` : '';

  document.getElementById('status').innerHTML =
    `<div class="row"><span class="k">CANFAR</span><span>${authOk ? '<span class="ok">authenticated</span>' : '<span class="warn">login needed</span>'} · <code>${esc(c.auth||'')}</code></span></div>` +
    `<div class="row"><span class="k">Manager</span><span>${mgrLabel}</span></div>` +
    wireRow + extra + hint;

  const btn = document.getElementById('btn-compute');
  if (btn && !btn.disabled) {
    if (r.compute_ready) btn.textContent = 'Refresh batch compute';
    else if (mgr || pending) btn.textContent = 'Finish batch compute';
    else btn.textContent = 'Start batch compute';
  }
}
function renderAgents(data) {
  const el = document.getElementById('agent-table');
  const agents = (data && data.agents) || [];
  if (!data || data.error && !agents.length) {
    el.innerHTML = `<p class="issues">${esc(data && (data.error || data.summary) || 'agent list failed')}</p>`;
    return;
  }
  if (!agents.length) {
    el.innerHTML = '<p class="lede-sm">No agents in the registry.</p>';
    return;
  }
  let rows = '';
  for (const row of agents) {
    if (!row || typeof row !== 'object') continue;
    const id = String(row.id || row.agent || '?');
    const binaryOk = !!(row.binary_ok || row.binary);
    const declared = row.config_declared !== false;
    const configOk = !!(row.config_ok || row.config);
    const cfg = declared && configOk;
    const name = (id === 'cursor' && binaryOk) ? 'cursor→agent' : id;
    const ver = String(row.version || '-').slice(0, 12);
    const install = binaryOk ? '' : `<button class="row-act" data-act="install" data-id="${esc(id)}">Install</button>`;
    const setup = `<button class="row-act secondary" data-act="setup" data-id="${esc(id)}">Setup</button>`;
    rows += `<tr class="${binaryOk ? 'on' : ''}">` +
      `<td>${esc(name)}</td>` +
      `<td>${mark(binaryOk)}</td>` +
      `<td>${mark(cfg)}</td>` +
      `<td>${esc(whereLabel(row))}</td>` +
      `<td>${esc(ver)}</td>` +
      `<td class="acts">${install}${setup}</td>` +
      `</tr>`;
  }
  const setup = (data && data.setup) || {};
  const stamp = setup.stamp || setup.last_run || '';
  const issues = Array.isArray(data.issues) ? data.issues.slice(0, 5) : [];
  el.innerHTML =
    `<table class="agent-list">` +
    `<thead><tr><th>Agent</th><th>Bin</th><th>Cfg</th><th>Where</th><th>Ver</th><th></th></tr></thead>` +
    `<tbody>${rows}</tbody></table>` +
    (stamp ? `<p class="stamp">Last setup: ${esc(String(stamp))}</p>` : '') +
    (issues.length ? `<p class="issues">${issues.map(esc).join('<br/>')}</p>` : '') +
    `<p class="stamp">Cfg: settings on $HOME · Where: scratch=$SCRATCH home=$HOME image=PATH</p>`;
}
async function refresh() {
  const [plat, agents] = await Promise.all([
    api('api/platform'),
    api('api/agents'),
  ]);
  renderStatus(plat.data || {});
  renderAgents(agents.data || {});
}
async function runAction(label, path, opts) {
  setBusy(true);
  setMsg(label + '…');
  try {
    const { data } = await api(path, opts || { method: 'POST' });
    const ok = !!(data && data.ok);
    const text = (data && (data.user_message || data.summary || data.error)) || (ok ? 'ok' : 'failed');
    const cls = ok ? 'ok' : (data && data.partial ? 'warn' : 'bad');
    setMsg(text, cls);
    await refresh();
  } catch (e) {
    setMsg(String(e), 'bad');
  } finally {
    setBusy(false);
  }
}
// Start batch compute: fire the background job, then poll /api/compute/status
// so the button shows live progress instead of blocking for many minutes.
const computeBtn = document.getElementById('btn-compute');
let ensurePoll = null;
async function pollEnsure() {
  const { data } = await api('api/compute/status');
  if (!data) return;
  if (data.running) {
    const steps = (data.steps || []).join(' › ');
    setMsg((steps ? steps + ' — ' : '') + 'waiting… (' + (data.elapsed || 0) + 's)', '');
    return; // keep polling until the worker finishes
  }
  clearInterval(ensurePoll);
  ensurePoll = null;
  computeBtn.disabled = false;
  const ok = !!(data && data.ok);
  setMsg(data.user_message || data.summary || (ok ? 'Batch compute ready.' : 'failed'),
         ok ? 'ok' : (data && data.partial ? 'warn' : 'bad'));
  await refresh();
}
computeBtn.onclick = async () => {
  computeBtn.disabled = true;
  setMsg('Starting batch compute…', '');
  try {
    await api('api/compute/ensure', { method: 'POST' });
    if (!ensurePoll) {
      pollEnsure().catch(() => {});
      ensurePoll = setInterval(() => { pollEnsure().catch(() => {}); }, 2000);
    }
  } catch (e) {
    computeBtn.disabled = false;
    setMsg(String(e), 'bad');
  }
};
// A reload mid-ensure must resume polling instead of showing an idle button.
(async function resumeEnsure() {
  try {
    const { data } = await api('api/compute/status');
    if (data && data.running) computeBtn.onclick();
  } catch (e) { /* hub sidecar not ready yet — the button still works */ }
})();
document.getElementById('agent-table').addEventListener('click', (ev) => {
  const node = ev.target && ev.target.nodeType === 1 ? ev.target : (ev.target && ev.target.parentElement);
  const btn = node && node.closest ? node.closest('button[data-act]') : null;
  if (!btn || btn.disabled) return;
  const id = btn.getAttribute('data-id');
  const act = btn.getAttribute('data-act');
  if (!id || !act) return;
  if (act === 'install') {
    runAction('Install ' + id, 'api/install?tool=' + encodeURIComponent(id), { method: 'POST' });
  } else if (act === 'setup') {
    runAction('Setup ' + id, 'api/setup?agent=' + encodeURIComponent(id), { method: 'POST' });
  }
});
refresh();
</script>
</body>
</html>
""".replace("__BACK_LABEL__", BACK_UI_LABEL).replace(
    "__BACK_LABEL_JSON__", json.dumps(BACK_UI_LABEL)
)


class WizardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("agent-wizard: %s\n" % (fmt % args))

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self._send(code, raw, "application/json; charset=utf-8")

    def _path(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urlparse(self.path)
        path = parsed.path
        for prefix in ("/astroai-agents",):
            if path.startswith(prefix):
                path = path[len(prefix) :] or "/"
        return path, parse_qs(parsed.query)

    def do_GET(self) -> None:
        path, qs = self._path()
        if path in ("/", "/index.html"):
            title = session_tab_title("AstroAI")
            html = INDEX_HTML.replace("<title>AstroAI</title>", f"<title>{title}</title>", 1)
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/platform":
            try:
                self._json(200, _platform_payload())
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(exc)})
            return
        if path in ("/api/agents", "/api/report"):
            try:
                code, payload = _agent_report()
                self._json(code, payload)
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(exc), "agents": []})
            return
        if path == "/api/addons":
            tag = (qs.get("tag") or ["lean"])[0]
            rc, rows, err = _plugins_from_list_config(tag)
            self._json(
                200 if rc in (0, 1) else 500,
                {
                    "ok": rc in (0, 1),
                    "addons": rows,
                    "tag": tag,
                    "cli_exit": rc,
                    **({} if rc in (0, 1) else {"error": err or "plugins list failed"}),
                },
            )
            return
        if path == "/healthz":
            self._json(200, {"ok": True})
            return
        if path == "/api/compute/status":
            try:
                self._json(200, _compute_status())
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(exc)})
            return
        self._send(404, b"not found\n", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        path, qs = self._path()
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)

        try:
            if path == "/api/setup":
                agent = _safe_agent_id((qs.get("agent") or [None])[0])
                if not agent:
                    self._json(400, {"ok": False, "error": "missing agent= query param"})
                    return
                self._json(200, _setup_payload(agent))
                return

            if path == "/api/install":
                tool = _safe_agent_id((qs.get("tool") or [None])[0])
                if not tool:
                    self._json(400, {"ok": False, "error": "missing tool= query param"})
                    return
                rc, out, err = _run_lab(["--yes", "--json", "agent", "install", tool])
                data = _parse_json_stdout(out) or {}
                if not isinstance(data, dict):
                    data = {}
                data["ok"] = rc == 0
                data["cli_exit"] = rc
                if rc == 0:
                    data["summary"] = f"installed {tool}"
                    data["user_message"] = f"installed {tool}"
                else:
                    errors = data.get("errors") or []
                    if isinstance(errors, list) and errors:
                        msg = "; ".join(str(e) for e in errors)[:300]
                    else:
                        msg = (err or out or "failed")[:300]
                    data["summary"] = msg
                    data["user_message"] = msg
                    data.setdefault("error", msg)
                self._json(200, data)
                return

            if path == "/api/compute/ensure":
                # Background job + polling: never hold the POST open.
                self._json(200, _start_compute_ensure())
                return

            # Kept for scripts; not exposed in lean UI.
            if path == "/api/verify":
                rc, out, err = _run_lab(["--json", "agent", "verify"], timeout=180)
                data = _parse_json_stdout(out) or {}
                if not isinstance(data, dict):
                    data = {}
                data["ok"] = rc == 0
                data["summary"] = "verify ok" if rc == 0 else (err or out or "verify failed")[:300]
                self._json(200, data)
                return

            if path == "/api/fix":
                rc, out, err = _run_lab(["--json", "agent", "verify", "--fix"], timeout=180)
                data = _parse_json_stdout(out) or {}
                if isinstance(data, list):
                    data = {"ok": rc == 0, "actions": data}
                if not isinstance(data, dict):
                    data = {"ok": rc == 0}
                data["ok"] = rc == 0
                data["summary"] = (
                    "verify --fix ok" if rc == 0 else (err or out or "verify --fix failed")[:300]
                )
                self._json(200, data)
                return

            if path == "/api/add":
                tag = (qs.get("tag") or [None])[0]
                name = (qs.get("name") or [None])[0]
                if name and not tag:
                    rc, out, err = _run_lab(
                        ["--yes", "--json", "agent", "plugins", "install", name]
                    )
                    data = _parse_json_stdout(out) or {}
                    if not isinstance(data, dict):
                        data = {}
                    data["ok"] = rc == 0
                    data["summary"] = (
                        f"plugin {name}" if rc == 0 else (err or out or "failed")[:300]
                    )
                    self._json(200, data)
                    return
                rc, data = _install_plugins_by_tag(tag or "lean")
                self._json(200, data)
                return

            self._send(404, b"not found\n", "text/plain; charset=utf-8")
        except Exception as exc:  # noqa: BLE001 — never crash the server loop
            self._json(
                500,
                {
                    "ok": False,
                    "error": str(exc),
                    "trace": traceback.format_exc()[-1500:],
                },
            )


def main() -> int:
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), WizardHandler)
    except OSError as exc:
        sys.stderr.write(f"agent-wizard: bind failed: {exc}\n")
        return 1
    sys.stderr.write(f"agent-wizard: listening 127.0.0.1:{PORT}\n")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
