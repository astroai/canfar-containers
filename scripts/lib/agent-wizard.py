"""AstroAI hub sidecar — agents, model keys, batch compute.

Listens on 127.0.0.1:ASTROAI_AGENT_WIZARD_PORT (default 4792).
Proxied as /astroai-agents/ (and /hub/ in Studio) by the session proxy.
Failures here must never affect the main UI process.

Surface:
  1. Model access — provider keys via `canfar-lab agent keys` (presence only)
  2. Coding agents — install / update / remove / setup as background jobs
  3. Compute — CANFAR auth, autoscaling ray-manager, OpenResearch wire
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import re
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
from session_title import stick_html_title  # noqa: E402

PORT = int(os.environ.get("ASTROAI_AGENT_WIZARD_PORT", "4792"))
CLI_TIMEOUT = int(os.environ.get("ASTROAI_AGENT_WIZARD_CLI_TIMEOUT", "600"))
MAX_BODY = 16 * 1024
PLATFORM_CANFAR_TIMEOUT = int(os.environ.get("ASTROAI_HUB_CANFAR_TIMEOUT", "12"))
COMPUTE_ENSURE_TIMEOUT = int(os.environ.get("ASTROAI_HUB_COMPUTE_ENSURE_TIMEOUT", "1200"))
RAY_MANAGER_IMAGE = os.environ.get(
    "RAY_MANAGER_IMAGE", "images.canfar.net/astroai/ray-manager:latest"
)
HOME = Path.home()
SESSION_KIND = (os.environ.get("ASTROAI_SESSION_KIND") or "").strip().lower()
BACK_UI_LABEL = {
    "openresearch": "OpenResearch",
    "studio": "Studio",
}.get(SESSION_KIND, "main UI")
HUB_TITLE = {
    "studio": "AstroAI Studio",
    "openresearch": "AstroAI",
}.get(SESSION_KIND, "AstroAI")
HUB_TITLE_SUFFIX = {"studio": "Studio"}.get(SESSION_KIND, "Hub")
# OpenResearch needs orx config wired to the Jobs URL. Studio only needs the
# ray-manager / Jobs URL (astroai cluster); do not require wire_orx.
WIRE_ORX = SESSION_KIND == "openresearch"
# Legacy alias used by older tests / call sites.
WIRE_OPENRESEARCH = WIRE_ORX


def _run_cmd(
    cmd: list[str], *, timeout: int, input_text: str | None = None
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
            input=input_text,
            stdin=None if input_text is not None else subprocess.DEVNULL,
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", f"{cmd[0]} not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    except OSError as exc:
        return 1, "", str(exc)


def _lab_bin() -> str:
    for candidate in (
        shutil.which("canfar-lab"),
        "/opt/astroai/venv/cadc/bin/canfar-lab",
        shutil.which("astroai"),
        "/opt/astroai/venv/cadc/bin/astroai",
    ):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return "canfar-lab"


def _run_lab(
    args: list[str], *, timeout: int | None = None, input_text: str | None = None
) -> tuple[int, str, str]:
    return _run_cmd([_lab_bin(), *args], timeout=timeout or CLI_TIMEOUT, input_text=input_text)


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


def _expiry_epoch(raw: object) -> float | None:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    if isinstance(raw, str) and raw.strip():
        from datetime import datetime, timezone

        try:
            parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)  # noqa: UP017 — image python may be 3.10
        return parsed.timestamp()
    return None


def _canfar_auth_line() -> tuple[bool, str]:
    """Logged in only with an active context whose credential has not expired.

    ``canfar auth show`` reports ``active: true`` for the default context even
    with no certificate at all (``expiry: null``), so presence alone is not auth.
    """
    if shutil.which("canfar") is None:
        return False, "canfar CLI not on PATH"
    rc, out, _err = _run_cmd(["canfar", "auth", "show", "--json"], timeout=PLATFORM_CANFAR_TIMEOUT)
    if rc == 124:
        return False, f"canfar auth show timed out after {PLATFORM_CANFAR_TIMEOUT}s"
    data = _parse_json_stdout(out)
    if rc != 0 or not isinstance(data, dict):
        return False, "not logged in — run `canfar login` in a terminal"
    expiry = _expiry_epoch(data.get("expiry"))
    who = str(data.get("name") or data.get("idp") or "CANFAR")
    if not data.get("active") or expiry is None:
        return False, "not logged in — run `canfar login` in a terminal"
    remaining = expiry - time.time()
    if remaining <= 0:
        return False, f"{who} login expired — run `canfar login` in a terminal"
    days = remaining / 86400
    left = f"{days:.0f} days" if days >= 1 else f"{remaining / 3600:.1f} hours"
    return True, f"{who} · {left} left"


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
    jobs = (os.environ.get("CANFAR_RAY_JOBS_ADDRESS") or "").strip().rstrip("/")
    if not jobs and connect:
        jobs = wire.jobs_url_from_connect(connect).rstrip("/")

    orx = _orx_wire_state(wire) if WIRE_ORX else {"wired": False, "address": "", "default_backend": ""}
    if WIRE_ORX and orx["address"] and not jobs:
        jobs = orx["address"]
    wired = bool(WIRE_ORX and orx["wired"] and jobs)
    compute_ready = bool(running) and (wired if WIRE_ORX else True)

    if compute_ready and WIRE_ORX:
        hint = "Batch compute ready — go back and run experiments."
    elif compute_ready:
        hint = "Batch compute ready — use `astroai run` / cluster jobs for heavy work."
    elif running and WIRE_ORX and not wired:
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
        "wire_supported": WIRE_ORX,
        "compute_ready": compute_ready,
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


# ---------------------------------------------------------------------------
# Agent jobs: install / update / remove / setup run one at a time in the
# background (an npm/curl install outlives any ingress request timeout).
# ---------------------------------------------------------------------------

JOB_VERBS: dict[str, tuple[str, ...]] = {
    "install": ("agent", "install"),
    "update": ("agent", "update"),
    "remove": ("agent", "remove"),
    "setup": ("agent", "setup"),
}
JOB_LOG_LINES = 300
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_JOB_LOCK = threading.Lock()
_JOB: dict[str, Any] = {
    "id": 0,
    "action": None,
    "agent": None,
    "running": False,
    "ok": None,
    "exit": None,
    "log": [],
    "started": 0.0,
    "finished": 0.0,
}


def _job_snapshot() -> dict[str, Any]:
    with _JOB_LOCK:
        job = dict(_JOB)
        job["log"] = list(_JOB["log"][-80:])
    end = job["finished"] or time.time()
    job["elapsed"] = round(max(0.0, end - job["started"])) if job["started"] else 0
    if job["action"] and not job["running"]:
        verb = {"install": "installed", "update": "updated", "remove": "removed", "setup": "set up"}
        job["summary"] = (
            f"{job['agent']} {verb[job['action']]}"
            if job["ok"]
            else f"{job['action']} {job['agent']} failed (exit {job['exit']})"
        )
    return job


def _job_worker(job_id: int, cmd: list[str]) -> None:
    env = os.environ.copy()
    env.update(NO_COLOR="1", TERM="dumb", COLUMNS="100", PYTHONUNBUFFERED="1")
    rc = 1
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            env=env,
        )
        timer = threading.Timer(CLI_TIMEOUT, proc.kill)
        timer.start()
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = _ANSI_RE.sub("", raw.rstrip("\n")).rstrip()
                if not line:
                    continue
                with _JOB_LOCK:
                    _JOB["log"] = [*_JOB["log"][-(JOB_LOG_LINES - 1) :], line]
            rc = proc.wait()
        finally:
            timer.cancel()
    except OSError as exc:
        with _JOB_LOCK:
            _JOB["log"] = [*_JOB["log"], f"could not start {cmd[0]}: {exc}"]
    with _JOB_LOCK:
        if _JOB["id"] == job_id:
            _JOB.update(running=False, ok=rc == 0, exit=rc, finished=time.time())


def _start_job(action: str, agent: str) -> tuple[int, dict[str, Any]]:
    if action not in JOB_VERBS:
        return 400, {"ok": False, "error": f"unknown action {action!r}"}
    with _JOB_LOCK:
        if _JOB["running"]:
            busy = f"busy: {_JOB['action']} {_JOB['agent']} is still running"
            return 409, {"ok": False, "error": busy}
        _JOB.update(
            id=_JOB["id"] + 1,
            action=action,
            agent=agent,
            running=True,
            ok=None,
            exit=None,
            log=[],
            started=time.time(),
            finished=0.0,
        )
        job_id = _JOB["id"]
    cmd = [_lab_bin(), "--yes", *JOB_VERBS[action], agent]
    threading.Thread(target=_job_worker, args=(job_id, cmd), daemon=True, name="agent-job").start()
    return 202, {"ok": True, **_job_snapshot()}


# ---------------------------------------------------------------------------
# Model API keys — values go to `canfar-lab agent keys set` on stdin and are
# never echoed back; the page only ever sees presence.
# ---------------------------------------------------------------------------

_KEY_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


def _keys_list() -> tuple[int, dict[str, Any]]:
    rc, out, err = _run_lab(["--json", "agent", "keys", "list"], timeout=60)
    data = _parse_json_stdout(out)
    if rc == 0 and isinstance(data, dict):
        return 200, {"ok": True, "keys": data.get("keys") or []}
    return 500, {"ok": False, "keys": [], "error": (err or out or "keys list failed")[:300]}


def _keys_change(name: str, value: str | None) -> tuple[int, dict[str, Any]]:
    if not _KEY_NAME_RE.match(name or ""):
        return 400, {"ok": False, "error": "invalid key name"}
    if value is None:
        rc, out, err = _run_lab(["--json", "agent", "keys", "unset", name], timeout=60)
    else:
        if not value.strip() or "\n" in value.strip():
            return 400, {"ok": False, "error": "paste a single-line key"}
        rc, out, err = _run_lab(
            ["--json", "agent", "keys", "set", name], timeout=60, input_text=value.strip() + "\n"
        )
    if rc == 0:
        return 200, {"ok": True, "key": name, "present": value is not None}
    try:
        payload = json.loads(out or "{}")
    except ValueError:
        payload = {}
    if isinstance(payload, dict) and payload.get("error"):
        message = " ".join(str(payload[k]) for k in ("error", "hint") if payload.get(k))
    else:
        lines = [ln.strip() for ln in (err or out or "").splitlines() if ln.strip()]
        message = next((ln for ln in lines if not ln.startswith(("Traceback", "File "))), "failed")
    return 400, {"ok": False, "key": name, "error": message[:300]}


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
        steps.append("create")
        return True, "ray-manager session created", steps
    # Name collision / already exists → treat as ok and continue ensure.
    if any(tok in text for tok in ("already", "conflict", "exists", "duplicate")):
        steps.append("create-exists")
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
    lab_prog = _lab_bin()
    if os.access(lab_prog, os.X_OK) or shutil.which(lab_prog):
        ensure_rc, ensure_out, ensure_err = _run_cmd(
            [
                lab_prog,
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
    if WIRE_ORX and jobs:
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

    ready = bool(jobs) and (not WIRE_ORX or bool(wired))
    if ready:
        msg = "Batch compute ready. Jobs with --cpus will add workers."
        if WIRE_ORX:
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
<title>__HUB_TITLE__</title>
<style>
  :root {
    --bg: #0b1026; --bg2: #0f1734; --card: #131c3d; --line: #25305a;
    --ink: #e8ecff; --muted: #98a2c8; --dim: #6c76a0;
    --blue: #38bdf8; --indigo: #6366f1; --violet: #a855f7;
    --grad: linear-gradient(135deg, #38bdf8, #6366f1 55%, #a855f7);
    --ok: #4ade80; --warn: #fbbf24; --err: #f87171;
    --sans: system-ui, -apple-system, "Segoe UI", Roboto, Ubuntu, sans-serif;
    --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; color: var(--ink); font: 15px/1.5 var(--sans);
    background: radial-gradient(ellipse 70% 45% at 15% -10%, rgba(99,102,241,.22), transparent 60%),
                radial-gradient(ellipse 50% 35% at 90% 0%, rgba(168,85,247,.14), transparent 55%),
                var(--bg);
  }
  .wrap { max-width: 60rem; margin: 0 auto; padding: 1.25rem clamp(1rem, 4vw, 2.5rem) 3rem; }
  header { display: flex; align-items: center; gap: .75rem; margin-bottom: 1.25rem; }
  header .brand { display: flex; align-items: center; gap: .55rem; font-weight: 650; font-size: 1.15rem; }
  header .brand span { color: var(--muted); font-weight: 500; }
  .back { margin-left: auto; color: var(--muted); text-decoration: none; font-size: .9rem;
          border: 1px solid var(--line); border-radius: 999px; padding: .3rem .8rem; }
  .back:hover { color: var(--ink); border-color: var(--indigo); }
  nav.tabs { display: flex; gap: .25rem; border-bottom: 1px solid var(--line); margin-bottom: 1.5rem; }
  nav.tabs a { color: var(--muted); text-decoration: none; padding: .55rem .9rem; font-weight: 600;
               border-bottom: 2px solid transparent; margin-bottom: -1px; }
  nav.tabs a.on { color: var(--ink); border-image: var(--grad) 1; border-bottom-width: 2px; border-bottom-style: solid; }
  h2 { font-size: 1.05rem; margin: 0 0 .2rem; }
  .lede { color: var(--muted); margin: 0 0 1rem; font-size: .93rem; }
  [hidden] { display: none !important; }
  section.panel { margin-bottom: 2.25rem; }
  .callout { border: 1px solid rgba(99,102,241,.55); background: rgba(99,102,241,.1);
             border-radius: 10px; padding: .75rem 1rem; margin-bottom: 1rem; font-size: .93rem; }
  .callout strong { color: #c7d2fe; }
  .list { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; background: var(--bg2); }
  .key-row { padding: .8rem 1rem; border-top: 1px solid var(--line); }
  .key-row:first-child { border-top: 0; }
  .key-line { display: flex; align-items: center; gap: .75rem; flex-wrap: wrap; }
  .key-name { font-weight: 600; min-width: 9rem; }
  .key-sub { color: var(--dim); font-size: .82rem; flex: 1 1 14rem; }
  .key-acts { display: flex; gap: .4rem; align-items: center; margin-left: auto; }
  .key-form { display: flex; gap: .4rem; margin-top: .6rem; }
  .key-form input { flex: 1; min-width: 0; font: .9rem var(--mono); color: var(--ink);
                    background: var(--bg); border: 1px solid var(--line); border-radius: 8px; padding: .5rem .65rem; }
  .key-form input:focus { outline: 2px solid var(--indigo); border-color: transparent; }
  .key-err { color: var(--err); font-size: .85rem; margin-top: .35rem; }
  .pill { display: inline-block; font-size: .72rem; font-weight: 600; border-radius: 999px;
          padding: .1rem .55rem; border: 1px solid var(--line); color: var(--muted); white-space: nowrap; }
  .pill.ok { color: var(--ok); border-color: rgba(74,222,128,.4); background: rgba(74,222,128,.08); }
  .pill.warn { color: var(--warn); border-color: rgba(251,191,36,.4); }
  a.ext { color: #a5b4fc; font-size: .85rem; text-decoration: none; white-space: nowrap; }
  a.ext:hover { text-decoration: underline; }
  button { font: 600 .85rem/1 var(--sans); border-radius: 8px; padding: .5rem .85rem; cursor: pointer;
           color: #fff; background: var(--grad); border: 0; white-space: nowrap; }
  button.secondary { background: transparent; color: var(--ink); border: 1px solid var(--line); }
  button.ghost { background: transparent; color: var(--muted); border: 0; padding: .5rem .5rem; }
  button:hover { filter: brightness(1.1); }
  button.secondary:hover { border-color: var(--indigo); }
  button:disabled { opacity: .45; cursor: not-allowed; filter: none; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr)); gap: .75rem; }
  .card { border: 1px solid var(--line); border-radius: 12px; background: var(--card); padding: .85rem .95rem;
          display: flex; flex-direction: column; gap: .45rem; }
  .card.on { border-color: rgba(99,102,241,.55); }
  .card-head { display: flex; align-items: center; gap: .5rem; }
  .card-head .name { font-weight: 650; }
  .card-head .pill { margin-left: auto; }
  .card p { margin: 0; color: var(--muted); font-size: .86rem; flex: 1; }
  .card-foot { display: flex; align-items: center; gap: .4rem; flex-wrap: wrap; }
  .card-foot .acts { margin-left: auto; display: flex; gap: .35rem; }
  code { font: .82em var(--mono); background: rgba(0,0,0,.3); border: 1px solid var(--line);
         border-radius: 5px; padding: .05rem .35rem; }
  details.more { margin-top: .9rem; }
  details.more summary { cursor: pointer; color: var(--muted); font-weight: 600; margin-bottom: .75rem; }
  #job { position: sticky; top: .5rem; z-index: 5; border: 1px solid var(--indigo); border-radius: 12px;
         background: rgba(15,23,52,.97); padding: .7rem .9rem; margin-bottom: 1.25rem;
         box-shadow: 0 10px 30px rgba(0,0,0,.35); }
  #job.ok { border-color: rgba(74,222,128,.6); } #job.bad { border-color: rgba(248,113,113,.7); }
  .job-head { display: flex; align-items: center; gap: .6rem; }
  .job-head .t { color: var(--dim); font-size: .85rem; margin-left: auto; }
  #job pre { margin: .55rem 0 0; max-height: 11rem; overflow: auto; font: .78rem/1.45 var(--mono);
             color: #c3cbef; white-space: pre-wrap; word-break: break-word; }
  .spin { width: .9rem; height: .9rem; border-radius: 50%; border: 2px solid var(--line);
          border-top-color: var(--violet); animation: spin .8s linear infinite; }
  #job.ok .spin, #job.bad .spin { display: none; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .status { border: 1px solid var(--line); border-radius: 12px; background: var(--bg2); padding: .6rem 1rem; }
  .row { display: grid; grid-template-columns: 8rem 1fr; gap: .25rem 1rem; padding: .35rem 0; align-items: baseline; }
  .row .k { color: var(--dim); font-size: .78rem; letter-spacing: .06em; text-transform: uppercase; }
  .ok { color: var(--ok); } .bad { color: var(--err); } .warn { color: var(--warn); }
  .actions { display: flex; gap: .5rem; margin: 1rem 0 .5rem; }
  #msg { min-height: 1.3rem; color: var(--muted); font-size: .92rem; white-space: pre-wrap; }
  #msg.ok { color: var(--ok); } #msg.warn { color: var(--warn); } #msg.bad { color: var(--err); }
  a.mgr { color: #a5b4fc; }
  .foot { margin-top: 2rem; color: var(--dim); font-size: .85rem; }
  .muted { color: var(--muted); }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">
      <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden="true">
        <defs><linearGradient id="hub-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#38bdf8"/><stop offset=".5" stop-color="#6366f1"/><stop offset="1" stop-color="#a855f7"/>
        </linearGradient></defs>
        <path d="M9.5 26V14.5a6.5 6.5 0 0 1 13 0V26h-3.2l-3.3-3.4-3.3 3.4z" fill="url(#hub-g)"/>
        <path d="M16 10.6l1.25 3.15 3.15 1.25-3.15 1.25L16 19.4l-1.25-3.15L11.6 15l3.15-1.25z" fill="#0b1026"/>
        <ellipse cx="16" cy="19.5" rx="13.5" ry="4" transform="rotate(-16 16 19.5)" fill="none"
                 stroke="url(#hub-g)" stroke-width="1.7" stroke-dasharray="30 6 44"/>
      </svg>
      AstroAI <span>__HUB_TITLE_SUFFIX__</span>
    </div>
    <a class="back" id="back-link" href="../">← Back to __BACK_LABEL__</a>
  </header>
  <nav class="tabs">
    <a href="#agents" data-tab="agents">Agents &amp; keys</a>
    <a href="#compute" data-tab="compute">Compute</a>
  </nav>

  <div id="tab-agents">
    <section id="job" hidden>
      <div class="job-head"><span class="spin"></span><strong id="job-title"></strong><span class="t" id="job-time"></span></div>
      <pre id="job-log"></pre>
    </section>

    <section class="panel">
      <h2>Model access</h2>
      <p class="lede">Agents need an API key from a model provider. Keys are saved privately in your
        home directory (readable only by you) and shared by the Assistant, terminal agents and marimo.
        A saved key is never shown again.</p>
      <div id="keys-callout"></div>
      <div class="list" id="keys">Loading…</div>
    </section>

    <section class="panel">
      <h2>Coding agents</h2>
      <p class="lede">Install puts the CLI on <code>$SCRATCH/.local/bin</code>; Set up writes its config,
        skills folders and CANFAR tools on <code>$HOME</code>. Run an installed agent from the
        <a class="ext" id="term-link" href="../terminal/">Terminal</a>.</p>
      <div id="agents">Loading…</div>
    </section>
    <p class="foot">Skills for CANFAR work: <code>npx skills add astroai/canfar-skills</code> ·
      Command line: <code>canfar-lab agent --help</code></p>
  </div>

  <div id="tab-compute" hidden>
    <section class="panel">
      <h2>Batch compute</h2>
      <p class="lede">Starts a ray-manager session with autoscaling workers (0–8 by default).
        Jobs submitted with <code>canfar-lab run</code> add workers on demand.</p>
      <div class="status" id="status">Loading…</div>
      <div class="actions"><button id="btn-compute">Start batch compute</button></div>
      <div id="msg"></div>
    </section>
    <p class="foot">Need <code>canfar login</code>? Run it in the <a class="ext" href="../terminal/" data-term>Terminal</a>, then come back.
      Command line: <code>canfar-lab cluster --help</code></p>
  </div>
</div>
<script>
const BACK_LABEL = __BACK_LABEL_JSON__;
const base = location.pathname.replace(/\\/?$/, '/');
const H = { 'X-AstroAI-Hub': '1' };

function referrerOrigin() {
  try { return new URL(document.referrer).origin; } catch (e) { return ''; }
}
function mainUiHref() {
  // 1. The page we came from (saved on first paint) — robust to any ingress
  //    shape, including root-mounted sessions.
  try {
    const saved = sessionStorage.getItem('astroai-hub-back');
    if (saved) return saved;
  } catch (e) { /* storage unavailable */ }
  if (document.referrer && referrerOrigin() === location.origin
      && !document.referrer.includes('/astroai-' + 'agents')) {
    return document.referrer;
  }
  // 2. Marker heuristic for direct loads with a session prefix.
  const p = location.pathname;
  const marker = '/astroai-' + 'agents';
  const i = p.lastIndexOf(marker);
  if (i > 0) return p.slice(0, i) + '/';
  const h = p.lastIndexOf('/hub/');
  if (h > 0) return p.slice(0, h) + '/';
  return '../';
}
function terminalHref() {
  const p = location.pathname;
  const h = p.lastIndexOf('/hub/');
  if (h >= 0) return p.slice(0, h) + '/terminal/';
  const i = p.lastIndexOf('/astroai-' + 'agents');
  if (i >= 0) return p.slice(0, i) + '/astroai-' + 'terminal/';
  return '../terminal/';
}
(function initLinks() {
  try {
    if (!sessionStorage.getItem('astroai-hub-back') && document.referrer
        && referrerOrigin() === location.origin) {
      const r = new URL(document.referrer);
      if (!r.pathname.includes('/astroai-' + 'agents') && !r.pathname.includes('/hub/')) {
        sessionStorage.setItem('astroai-hub-back', r.pathname + r.search);
      }
    }
  } catch (e) { /* ignore */ }
  const a = document.getElementById('back-link');
  a.href = mainUiHref();
  a.textContent = '← Back to ' + BACK_LABEL;
  document.getElementById('term-link').href = terminalHref();
  document.querySelectorAll('a[data-term]').forEach(n => { n.href = terminalHref(); });
})();

async function api(path, opts) {
  opts = opts || {};
  const init = { method: opts.method || 'GET', headers: Object.assign({}, H) };
  if (opts.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  const r = await fetch(base + path.replace(/^\\//, ''), init);
  const text = await r.text();
  let data;
  try { data = JSON.parse(text); } catch (e) { data = { ok: false, error: text.slice(0, 300) }; }
  return { status: r.status, data };
}
function esc(s) {
  return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── tabs ──────────────────────────────────────────────────────────────
let computeLoaded = false;
function showTab() {
  const tab = location.hash === '#compute' ? 'compute' : 'agents';
  document.getElementById('tab-agents').hidden = tab !== 'agents';
  document.getElementById('tab-compute').hidden = tab !== 'compute';
  document.querySelectorAll('nav.tabs a').forEach(a => a.classList.toggle('on', a.dataset.tab === tab));
  if (tab === 'compute' && !computeLoaded) { computeLoaded = true; refreshCompute(); }
}
window.addEventListener('hashchange', showTab);

// ── model keys ────────────────────────────────────────────────────────
let keyRows = [];
function renderKeys() {
  const el = document.getElementById('keys');
  if (!keyRows.length) { el.innerHTML = '<div class="key-row muted">No key catalog (canfar-lab missing?)</div>'; return; }
  const any = keyRows.some(k => k.present);
  document.getElementById('keys-callout').innerHTML = any ? '' :
    '<div class="callout"><strong>Start here:</strong> add one key. OpenRouter is the simplest — ' +
    'one key works with most agents and with marimo. Use a provider key (Anthropic, OpenAI, …) ' +
    'if you already have one.</div>';
  el.innerHTML = keyRows.map(k => {
    const stored = (k.sources || []).some(s => s === 'studio' || s === 'assistant');
    const envOnly = k.present && !stored;
    const pill = k.present
      ? (envOnly ? '<span class="pill warn">from environment</span>' : '<span class="pill ok">saved</span>')
      : '<span class="pill">not set</span>';
    const used = (k.used_by || []).slice(0, 5).join(', ') + ((k.used_by || []).length > 5 ? ', …' : '');
    const link = k.signup_url ? `<a class="ext" href="${esc(k.signup_url)}" target="_blank" rel="noopener">Get a key ↗</a>` : '';
    const edit = `<button class="secondary" data-kact="edit">${k.present ? 'Replace' : 'Add key'}</button>`;
    const remove = stored ? '<button class="ghost" data-kact="remove">Remove</button>' : '';
    return `<div class="key-row" data-key="${esc(k.key)}">` +
      `<div class="key-line"><span class="key-name">${esc(k.label)}</span>${pill}` +
      `<span class="key-sub">${used ? 'Used by ' + esc(used) : esc(k.key)}</span>` +
      `<span class="key-acts">${link}${edit}${remove}</span></div>` +
      `<form class="key-form" hidden><input type="password" autocomplete="off" spellcheck="false" ` +
      `aria-label="${esc(k.key)}" placeholder="Paste your ${esc(k.label)} key (${esc(k.key)})"/>` +
      `<button type="submit">Save</button><button type="button" class="ghost" data-kact="cancel">Cancel</button></form>` +
      `<div class="key-err" hidden></div></div>`;
  }).join('');
}
async function loadKeys() {
  const { data } = await api('api/keys');
  keyRows = (data && data.keys) || [];
  renderKeys();
  if (data && !data.ok && data.error) {
    document.getElementById('keys').insertAdjacentHTML('beforeend', `<div class="key-row key-err">${esc(data.error)}</div>`);
  }
}
document.getElementById('keys').addEventListener('click', async (ev) => {
  const btn = ev.target.closest && ev.target.closest('[data-kact]');
  if (!btn) return;
  const row = btn.closest('.key-row');
  const form = row.querySelector('.key-form');
  const err = row.querySelector('.key-err');
  const act = btn.dataset.kact;
  if (act === 'edit') { form.hidden = false; form.querySelector('input').focus(); }
  if (act === 'cancel') { form.hidden = true; form.querySelector('input').value = ''; err.hidden = true; }
  if (act === 'remove') {
    if (!confirm('Remove the saved ' + row.dataset.key + '?')) return;
    btn.disabled = true;
    const { data } = await api('api/keys', { method: 'POST', body: { key: row.dataset.key, value: null } });
    if (!data.ok) { err.textContent = data.error || 'failed'; err.hidden = false; btn.disabled = false; return; }
    await loadKeys();
  }
});
document.getElementById('keys').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const form = ev.target;
  const row = form.closest('.key-row');
  const input = form.querySelector('input');
  const err = row.querySelector('.key-err');
  const value = input.value.trim();
  if (!value) { input.focus(); return; }
  form.querySelectorAll('button').forEach(b => { b.disabled = true; });
  const { data } = await api('api/keys', { method: 'POST', body: { key: row.dataset.key, value } });
  input.value = '';
  form.querySelectorAll('button').forEach(b => { b.disabled = false; });
  if (!data.ok) { err.textContent = data.error || 'failed'; err.hidden = false; return; }
  await loadKeys();
});

// ── agents ────────────────────────────────────────────────────────────
let jobRunning = false;
function agentCard(row) {
  const id = String(row.id || row.agent || '?');
  const installed = !!(row.binary_ok || row.binary);
  const managed = !!row.managed;
  const builtIn = installed && !managed;
  const ver = row.version ? String(row.version).replace(/^v/, '').slice(0, 14) : '';
  const pill = installed
    ? `<span class="pill ok">${builtIn ? 'built in' : 'installed'}${ver ? ' · ' + esc(ver) : ''}</span>`
    : '<span class="pill">not installed</span>';
  const name = id === 'dsh' ? 'AstroAI Assistant (dsh)' : (row.name || id);
  const acts = [];
  if (!installed) acts.push(`<button data-act="install" data-id="${esc(id)}">Install</button>`);
  else {
    acts.push(`<button class="secondary" data-act="setup" data-id="${esc(id)}">Set up</button>`);
    if (managed) {
      acts.push(`<button class="ghost" data-act="update" data-id="${esc(id)}">Update</button>`);
      acts.push(`<button class="ghost" data-act="remove" data-id="${esc(id)}">Remove</button>`);
    }
  }
  const run = installed && row.binary_name ? `<code>${esc(row.binary_name)}</code>` : '';
  return `<div class="card ${installed ? 'on' : ''}"><div class="card-head"><span class="name">${esc(name)}</span>${pill}</div>` +
    `<p>${esc(row.summary || '')}</p>` +
    `<div class="card-foot">${run}<span class="acts">${acts.join('')}</span></div></div>`;
}
async function loadAgents() {
  const el = document.getElementById('agents');
  const { data } = await api('api/agents');
  const agents = (data && data.agents) || [];
  if (!agents.length) {
    el.innerHTML = `<p class="bad">${esc((data && (data.error || data.summary)) || 'agent list failed')}</p>`;
    return;
  }
  const rec = (data.supported || []).map(String);
  const byId = Object.fromEntries(agents.map(a => [String(a.id || a.agent), a]));
  const top = rec.filter(id => byId[id]).map(id => byId[id]);
  const rest = agents.filter(a => !rec.includes(String(a.id || a.agent)));
  el.innerHTML = `<div class="grid">${top.map(agentCard).join('')}</div>` +
    (rest.length ? `<details class="more"><summary>More agents (${rest.length})</summary>` +
      `<div class="grid">${rest.map(agentCard).join('')}</div></details>` : '');
  el.querySelectorAll('button[data-act]').forEach(b => { b.disabled = jobRunning; });
}
document.getElementById('agents').addEventListener('click', async (ev) => {
  const btn = ev.target.closest && ev.target.closest('button[data-act]');
  if (!btn || btn.disabled) return;
  const act = btn.dataset.act, id = btn.dataset.id;
  if (act === 'remove' && !confirm('Remove ' + id + ' from $SCRATCH/.local/bin?')) return;
  const { status, data } = await api('api/jobs', { method: 'POST', body: { action: act, agent: id } });
  if (status >= 400) { showJob({ action: act, agent: id, running: false, ok: false, log: [data.error || 'failed'] }); return; }
  showJob(data);
  pollJob();
});
function showJob(job) {
  const box = document.getElementById('job');
  if (!job || !job.action) { box.hidden = true; return; }
  box.hidden = false;
  box.className = job.running ? '' : (job.ok ? 'ok' : 'bad');
  const verb = { install: 'Installing', update: 'Updating', remove: 'Removing', setup: 'Setting up' }[job.action];
  document.getElementById('job-title').textContent = job.running ? `${verb} ${job.agent}…` : (job.summary || (job.ok ? 'done' : 'failed'));
  document.getElementById('job-time').textContent = job.elapsed ? job.elapsed + 's' : '';
  const pre = document.getElementById('job-log');
  pre.textContent = (job.log || []).join('\\n');
  pre.scrollTop = pre.scrollHeight;
  jobRunning = !!job.running;
  document.querySelectorAll('#agents button[data-act]').forEach(b => { b.disabled = jobRunning; });
}
let jobTimer = null;
async function pollJob() {
  if (jobTimer) return;
  jobTimer = setInterval(async () => {
    const { data } = await api('api/jobs');
    showJob(data);
    if (!data.running) {
      clearInterval(jobTimer); jobTimer = null;
      await Promise.all([loadAgents(), loadKeys()]);
    }
  }, 1500);
}

// ── compute ───────────────────────────────────────────────────────────
function setMsg(t, cls) {
  const el = document.getElementById('msg');
  el.textContent = t || '';
  el.className = cls || '';
}
function renderStatus(p) {
  const c = (p && p.canfar) || {};
  const r = (p && p.ray) || {};
  const mgr = !!r.manager_running, pending = !!r.manager_pending;
  let mgrLabel = '<span class="muted">none</span>';
  if (mgr) mgrLabel = '<span class="ok">Running</span>';
  else if (pending) mgrLabel = '<span class="warn">Pending</span>';
  let rows = `<div class="row"><span class="k">CANFAR</span><span>${c.auth_ok ? '<span class="ok">logged in</span>' : '<span class="warn">login needed</span>'} · ${esc(c.auth || '')}</span></div>` +
    `<div class="row"><span class="k">Manager</span><span>${mgrLabel}</span></div>`;
  if (r.wire_supported) {
    rows += `<div class="row"><span class="k">OpenResearch</span><span>${r.orx_wired ? '<span class="ok">wired</span>' : '<span class="warn">not wired</span>'}</span></div>`;
  }
  if (r.connect_url && mgr) {
    rows += `<div class="row"><span class="k">Dashboard</span><span><a class="mgr" href="${esc(r.connect_url)}" target="_blank" rel="noopener">open ↗</a></span></div>`;
  }
  if (r.ray_address) rows += `<div class="row"><span class="k">Jobs URL</span><span><code>${esc(r.ray_address)}</code></span></div>`;
  if (r.hint) rows += `<div class="row"><span class="k">Note</span><span>${esc(r.hint)}</span></div>`;
  document.getElementById('status').innerHTML = rows;
  const btn = document.getElementById('btn-compute');
  if (btn && !btn.disabled) {
    if (r.compute_ready) btn.textContent = 'Refresh batch compute';
    else if (mgr || pending) btn.textContent = 'Finish batch compute';
    else btn.textContent = 'Start batch compute';
  }
}
async function refreshCompute() {
  const { data } = await api('api/platform');
  renderStatus(data || {});
}
const computeBtn = document.getElementById('btn-compute');
let ensurePoll = null;
async function pollEnsure() {
  const { data } = await api('api/compute/status');
  if (!data) return;
  if (data.running) {
    const steps = (data.steps || []).join(' › ');
    setMsg((steps ? steps + ' — ' : '') + 'waiting… (' + (data.elapsed || 0) + 's)', '');
    return;
  }
  clearInterval(ensurePoll);
  ensurePoll = null;
  computeBtn.disabled = false;
  const ok = !!data.ok;
  setMsg(data.user_message || data.summary || (ok ? 'Batch compute ready.' : 'failed'),
         ok ? 'ok' : (data.partial ? 'warn' : 'bad'));
  await refreshCompute();
}
computeBtn.onclick = async () => {
  computeBtn.disabled = true;
  setMsg('Starting batch compute…', '');
  try {
    await api('api/compute/ensure', { method: 'POST', body: {} });
    if (!ensurePoll) {
      pollEnsure().catch(() => {});
      ensurePoll = setInterval(() => { pollEnsure().catch(() => {}); }, 2000);
    }
  } catch (e) {
    computeBtn.disabled = false;
    setMsg(String(e), 'bad');
  }
};

// ── boot ──────────────────────────────────────────────────────────────
showTab();
loadKeys();
loadAgents();
(async function resume() {
  try {
    const job = await api('api/jobs');
    if (job.data && job.data.running) { showJob(job.data); pollJob(); }
    const ens = await api('api/compute/status');
    if (ens.data && ens.data.running) { location.hash = '#compute'; computeBtn.onclick(); }
  } catch (e) { /* hub sidecar not ready yet */ }
})();
</script>
</body>
</html>
""".replace("__BACK_LABEL__", BACK_UI_LABEL).replace(
    "__BACK_LABEL_JSON__", json.dumps(BACK_UI_LABEL)
).replace("__HUB_TITLE_SUFFIX__", HUB_TITLE_SUFFIX).replace("__HUB_TITLE__", HUB_TITLE)


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
            html = stick_html_title(INDEX_HTML, HUB_TITLE)
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
        if path == "/api/keys":
            code, payload = _keys_list()
            self._json(code, payload)
            return
        if path == "/api/jobs":
            self._json(200, _job_snapshot())
            return
        if path == "/api/compute/status":
            try:
                self._json(200, _compute_status())
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(exc)})
            return
        self._send(404, b"not found\n", "text/plain; charset=utf-8")

    def _body(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > MAX_BODY:
            return None
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def do_POST(self) -> None:
        path, qs = self._path()
        # A custom header forces a CORS preflight, which this server never
        # grants: other sites cannot drive installs or overwrite keys.
        if self.headers.get("X-AstroAI-Hub") != "1":
            self._json(403, {"ok": False, "error": "missing X-AstroAI-Hub header"})
            return
        body = self._body()
        if body is None:
            self._json(400, {"ok": False, "error": "expected a small JSON object body"})
            return

        try:
            if path == "/api/keys":
                name = str(body.get("key") or "")
                value = body.get("value")
                if value is not None and not isinstance(value, str):
                    self._json(400, {"ok": False, "error": "value must be a string or null"})
                    return
                code, payload = _keys_change(name, value)
                self._json(code, payload)
                return

            if path == "/api/jobs":
                agent = _safe_agent_id(str(body.get("agent") or ""))
                if not agent:
                    self._json(400, {"ok": False, "error": "missing or invalid agent"})
                    return
                code, payload = _start_job(str(body.get("action") or ""), agent)
                self._json(code, payload)
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
