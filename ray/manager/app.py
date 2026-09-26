"""CANFAR Ray Manager web app — cluster lifecycle (Milestone C)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from canfar_workload.canfar_ops import CanfarOps
from canfar_workload.cluster import (
    ClusterCreateRequest,
    clean_orphaned_workers,
    create_cluster,
    gc_terminal_cluster_workers,
    retry_worker,
    stop_cluster,
    validate_cluster_create,
)
from canfar_workload.preflight import run_preflight
from canfar_workload.ray_cluster import count_live_nodes, list_ray_nodes, ray_address, ray_running
from canfar_workload.reconcile import reconcile_cluster
from canfar_workload.settings import ManagerSettings, manager_pod_ip
from canfar_workload.state_store import ClusterState, StateStore
from canfar_workload.worker_logs import archive_session_logs, read_worker_logs
from canfar_workload.workers import destroy_all_workers, destroy_worker, launch_worker
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from background_tasks import active_operation, start_background
from dashboard_proxy import dashboard_ready
from dashboard_proxy import router as dashboard_router
from ui import (
    PAGE_STYLE,
    RETRY_PHASES,
    flash_html,
    phase_class,
    public_path,
    redirect_with_flash,
    setup_checklist_html,
    setup_ready,
    workers_table_html,
)


def _tab_title() -> str:
    lib = "/opt/astroai/lib"
    if lib not in sys.path:
        sys.path.insert(0, lib)
    try:
        from session_title import session_tab_title
    except ImportError:
        return "CANFAR Ray Manager"
    return session_tab_title("CANFAR Ray Manager")

_ray_head_proc: subprocess.Popen[str] | None = None
_settings = ManagerSettings.from_env()
_store = StateStore(_settings.cluster_id)
_canfar = CanfarOps()


def _heartbeat_path() -> Path:
    return _store.dir / "manager-heartbeat"


class WorkerLaunchRequest(BaseModel):
    cores: int = Field(default=1, ge=1, le=32)
    ram_gb: int = Field(default=4, ge=1, le=128)
    gpus: int = Field(default=0, ge=0, le=8)
    require_preflight: bool = True


class ClusterCreateBody(BaseModel):
    name: str = Field(default="", max_length=64)
    worker_count: int = Field(default=2, ge=1, le=16)
    cores: int = Field(default=1, ge=1, le=32)
    ram_gb: int = Field(default=4, ge=1, le=128)
    gpus: int = Field(default=0, ge=0, le=8)
    min_joined: int | None = Field(default=None, ge=1, le=16)
    partial_policy: Literal["fail_and_cleanup", "accept_partial", "continue_waiting"] = "accept_partial"
    require_preflight: bool = True


def _drain_head_output(stream: Any) -> None:
    """Forward ray-head-start.sh output to our stdout.

    Without a drainer, the Popen pipe fills (~64 KiB) and the child blocks; and
    the head-start diagnostics (e.g. "Ray autoscaler enabled") never reach the
    container stdout that `canfar logs` / operators read.
    """
    assert stream is not None
    for line in iter(stream.readline, ""):
        sys.stdout.write(f"[ray-head] {line}")
        sys.stdout.flush()


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    global _ray_head_proc
    _store.ensure_dir()
    if not ray_running():
        _ray_head_proc = subprocess.Popen(
            ["/opt/astroai/bin/ray-head-start.sh"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        threading.Thread(
            target=_drain_head_output,
            args=(_ray_head_proc.stdout,),
            daemon=True,
            name="ray-head-log-drain",
        ).start()
    gc_terminal_cluster_workers(settings=_settings, canfar=_canfar, store=_store)
    yield


app = FastAPI(title="CANFAR Ray Manager", lifespan=lifespan)
app.include_router(dashboard_router)


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/readyz")
def readyz() -> JSONResponse:
    scratch = Path(os.environ.get("SCRATCH", "/scratch"))
    if not scratch.is_dir() or not os.access(scratch, os.W_OK):
        return JSONResponse({"ready": False, "reason": "scratch unavailable"}, status_code=503)
    if not ray_running():
        return JSONResponse({"ready": False, "reason": "ray head unavailable"}, status_code=503)
    return JSONResponse({"ready": True, "ray_address": ray_address()})


@app.get("/api/v1/auth/status")
def api_auth_status() -> JSONResponse:
    return JSONResponse(asdict(_canfar.auth_status()))


@app.get("/api/v1/status")
def api_status() -> JSONResponse:
    nodes = list_ray_nodes()
    state = reconcile_cluster(canfar=_canfar, store=_store, nodes=nodes)
    return JSONResponse(_cluster_payload(state, nodes=nodes))


@app.post("/api/v1/cluster/reconcile")
def api_cluster_reconcile() -> JSONResponse:
    nodes = list_ray_nodes()
    state = reconcile_cluster(canfar=_canfar, store=_store, nodes=nodes)
    return JSONResponse(_cluster_payload(state, nodes=nodes))


def _cluster_create_request(body: ClusterCreateBody) -> ClusterCreateRequest:
    return ClusterCreateRequest(
        name=body.name or _settings.cluster_id,
        worker_count=body.worker_count,
        cores=body.cores,
        ram_gb=body.ram_gb,
        gpus=body.gpus,
        min_joined=body.min_joined,
        partial_policy=body.partial_policy,
        require_preflight=body.require_preflight,
    )


def _start_cluster_create(req: ClusterCreateRequest) -> None:
    create_cluster(
        settings=_settings,
        canfar=_canfar,
        store=_store,
        heartbeat_path=str(_heartbeat_path()),
        req=req,
    )


@app.post("/api/v1/preflight/run")
def api_preflight_run(async_mode: bool = Query(default=False, alias="async")) -> JSONResponse:
    if async_mode:
        op = active_operation()
        if op and op.running:
            raise HTTPException(status_code=409, detail=f"Operation in progress: {op.kind}")
        if not start_background("preflight", lambda: run_preflight(_settings, _canfar, _store)):
            raise HTTPException(status_code=409, detail="Operation already in progress")
        payload = _cluster_payload(_store.load())
        payload["accepted"] = True
        return JSONResponse(payload, status_code=202)

    report = run_preflight(_settings, _canfar, _store)
    code = 200 if report.passed else 503
    return JSONResponse(report.as_dict(), status_code=code)


@app.post("/api/v1/cluster/create")
def api_cluster_create(
    body: ClusterCreateBody,
    async_mode: bool = Query(default=False, alias="async"),
) -> JSONResponse:
    req = _cluster_create_request(body)
    try:
        validate_cluster_create(canfar=_canfar, store=_store, req=req)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if async_mode:
        op = active_operation()
        if op and op.running:
            raise HTTPException(status_code=409, detail=f"Operation in progress: {op.kind}")
        if not start_background("cluster_create", lambda: _start_cluster_create(req)):
            raise HTTPException(status_code=409, detail="Operation already in progress")
        payload = _cluster_payload(_store.load())
        payload["accepted"] = True
        return JSONResponse(payload, status_code=202)

    try:
        result = create_cluster(
            settings=_settings,
            canfar=_canfar,
            store=_store,
            heartbeat_path=str(_heartbeat_path()),
            req=req,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = _cluster_payload(result.state)
    payload["success"] = result.success
    payload["message"] = result.message
    code = 200 if result.success else 503
    return JSONResponse(payload, status_code=code)


@app.post("/api/v1/cluster/stop")
def api_cluster_stop() -> JSONResponse:
    state = stop_cluster(canfar=_canfar, store=_store)
    return JSONResponse(_cluster_payload(state))


@app.post("/api/v1/cluster/clean-orphans")
def api_cluster_clean_orphans() -> JSONResponse:
    destroyed = clean_orphaned_workers(settings=_settings, canfar=_canfar, store=_store)
    return JSONResponse({"destroyed": destroyed})


@app.post("/api/v1/workers/launch")
def api_workers_launch(body: WorkerLaunchRequest) -> JSONResponse:
    try:
        result = launch_worker(
            settings=_settings,
            canfar=_canfar,
            store=_store,
            heartbeat_path=str(_heartbeat_path()),
            cores=body.cores,
            ram_gb=body.ram_gb,
            gpus=body.gpus,
            require_preflight=body.require_preflight,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload: dict[str, Any] = {"worker": asdict(result.worker)}
    if result.logs_excerpt:
        payload["logs_excerpt"] = result.logs_excerpt
    code = 200 if result.worker.ray_joined else 503
    return JSONResponse(payload, status_code=code)


@app.post("/api/v1/workers/{session_id}/retry")
def api_workers_retry(session_id: str) -> JSONResponse:
    try:
        worker = retry_worker(
            settings=_settings,
            canfar=_canfar,
            store=_store,
            heartbeat_path=str(_heartbeat_path()),
            session_id=session_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    code = 200 if worker.ray_joined else 503
    return JSONResponse({"worker": asdict(worker)}, status_code=code)


@app.get("/api/v1/workers/{session_id}/logs")
def api_worker_logs(session_id: str, refresh: bool = Query(default=False)) -> PlainTextResponse:
    if refresh:
        state = _store.load()
        worker = next((w for w in state.workers if w.session_id == session_id), None) if state else None
        archive_session_logs(
            canfar=_canfar,
            store=_store,
            session_id=session_id,
            worker=worker,
            state=state,
        )
    text = read_worker_logs(_store, session_id)
    if text is None:
        raise HTTPException(status_code=404, detail="No saved logs for this worker session")
    return PlainTextResponse(text)


@app.delete("/api/v1/workers/{session_id}")
def api_workers_destroy(session_id: str) -> JSONResponse:
    return JSONResponse(destroy_worker(canfar=_canfar, store=_store, session_id=session_id))


@app.post("/api/v1/workers/destroy-all")
def api_workers_destroy_all() -> JSONResponse:
    results = destroy_all_workers(canfar=_canfar, store=_store)
    return JSONResponse({"destroyed": results})


@app.get("/api/v1/ray/nodes")
def api_ray_nodes() -> JSONResponse:
    nodes = list_ray_nodes()
    return JSONResponse({"nodes": nodes, "alive": count_live_nodes(nodes=nodes)})


@app.post("/actions/preflight")
def action_preflight() -> RedirectResponse:
    op = active_operation()
    if op and op.running:
        return RedirectResponse(
            redirect_with_flash("/", "warn", f"Already running: {op.kind}"),
            status_code=303,
        )
    if not start_background("preflight", lambda: run_preflight(_settings, _canfar, _store)):
        return RedirectResponse(
            redirect_with_flash("/", "warn", "Could not start preflight"),
            status_code=303,
        )
    return RedirectResponse(
        redirect_with_flash("/", "ok", "Network preflight started — refresh for results"),
        status_code=303,
    )


@app.post("/actions/create-cluster")
def action_create_cluster(
    worker_count: int = Form(2),
    cores: int = Form(1),
    ram_gb: int = Form(4),
    gpus: int = Form(0),
    min_joined: int = Form(2),
    partial_policy: str = Form("accept_partial"),
) -> RedirectResponse:
    req = ClusterCreateRequest(
        name=_settings.cluster_id,
        worker_count=worker_count,
        cores=cores,
        ram_gb=ram_gb,
        gpus=gpus,
        min_joined=min_joined,
        partial_policy=partial_policy,  # type: ignore[arg-type]
        require_preflight=True,
    )
    try:
        validate_cluster_create(canfar=_canfar, store=_store, req=req)
    except RuntimeError as exc:
        return RedirectResponse(redirect_with_flash("/", "error", str(exc)), status_code=303)

    op = active_operation()
    if op and op.running:
        return RedirectResponse(
            redirect_with_flash("/", "warn", f"Already running: {op.kind}"),
            status_code=303,
        )
    if not start_background("cluster_create", lambda: _start_cluster_create(req)):
        return RedirectResponse(
            redirect_with_flash("/", "warn", "Could not start cluster create"),
            status_code=303,
        )
    return RedirectResponse(
        redirect_with_flash("/", "ok", "Cluster create started — refresh for progress"),
        status_code=303,
    )


@app.post("/actions/stop-cluster")
def action_stop_cluster() -> RedirectResponse:
    stop_cluster(canfar=_canfar, store=_store)
    return RedirectResponse(redirect_with_flash("/", "ok", "Cluster stopped"), status_code=303)


@app.post("/actions/reconcile")
def action_reconcile() -> RedirectResponse:
    nodes = list_ray_nodes()
    state = reconcile_cluster(canfar=_canfar, store=_store, nodes=nodes)
    joined = len(_store.joined_workers(state)) if state else 0
    return RedirectResponse(
        redirect_with_flash("/", "ok", f"Reconciled — {joined} worker(s) joined"),
        status_code=303,
    )


@app.post("/actions/clean-orphans")
def action_clean_orphans() -> RedirectResponse:
    try:
        destroyed = clean_orphaned_workers(settings=_settings, canfar=_canfar, store=_store)
    except RuntimeError as exc:
        return RedirectResponse(redirect_with_flash("/", "error", str(exc)), status_code=303)
    return RedirectResponse(
        redirect_with_flash("/", "ok", f"Cleaned {len(destroyed)} orphaned session(s)"),
        status_code=303,
    )


@app.post("/actions/retry-worker/{session_id}")
def action_retry_worker(session_id: str) -> RedirectResponse:
    try:
        worker = retry_worker(
            settings=_settings,
            canfar=_canfar,
            store=_store,
            heartbeat_path=str(_heartbeat_path()),
            session_id=session_id,
        )
    except RuntimeError as exc:
        return RedirectResponse(redirect_with_flash("/", "error", str(exc)), status_code=303)
    msg = f"Retry {worker.name}: {'joined' if worker.ray_joined else worker.phase}"
    flash = "ok" if worker.ray_joined else "warn"
    return RedirectResponse(redirect_with_flash("/", flash, msg), status_code=303)


@app.get("/api/v1/dashboard/status")
def api_dashboard_status() -> JSONResponse:
    return JSONResponse(
        {
            "ready": dashboard_ready(),
            "path": public_path("/dashboard/"),
            "upstream": "127.0.0.1:8265",
        }
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> str:
    auth = _canfar.auth_status()
    nodes = list_ray_nodes()
    state = reconcile_cluster(canfar=_canfar, store=_store, nodes=nodes)
    preflight = (state.preflight if state else None) or {}
    flash = request.query_params.get("flash")
    flash_msg = request.query_params.get("msg")
    live_nodes = count_live_nodes(nodes=nodes)
    dash_ok = dashboard_ready()
    op = active_operation()

    # Browser-visible paths (include /session/contrib/<id> on CANFAR).
    p_dash = public_path("/dashboard/")
    p_status = public_path("/api/v1/status")
    p_dash_status = public_path("/api/v1/dashboard/status")
    p_auth = public_path("/api/v1/auth/status")
    p_health = public_path("/healthz")
    p_create = public_path("/actions/create-cluster")
    p_preflight = public_path("/actions/preflight")
    p_reconcile = public_path("/actions/reconcile")
    p_stop = public_path("/actions/stop-cluster")
    p_orphans = public_path("/actions/clean-orphans")
    ui_paths = _ui_paths()

    worker_entries = _worker_ui_entries(state)
    workers_html = workers_table_html(
        worker_entries,
        retry_action_prefix=ui_paths["retry_worker_prefix"],
        logs_href_prefix=ui_paths["worker_logs_prefix"],
    )
    cluster_ready = setup_ready(authenticated=auth.authenticated, preflight=preflight)
    checklist_html = setup_checklist_html(
        authenticated=auth.authenticated,
        auth_idp=auth.idp,
        preflight=preflight,
        preflight_action=p_preflight,
        ready=cluster_ready,
    )
    create_disabled = "" if cluster_ready else " disabled"
    cluster_phase = state.phase if state else "Idle"
    joined = len(_store.joined_workers(state)) if state else 0
    target = state.worker_count if state else 0
    progress_pct = 0
    if target > 0:
        progress_pct = min(100, int(round(100.0 * joined / target)))
    dash_cta = (
        f'<a class="btn btn-primary" href="{p_dash}" target="_blank" rel="noopener">'
        "Open Ray Dashboard</a>"
        if dash_ok
        else (
            f'<a class="btn btn-primary" href="{p_dash}" '
            'title="Dashboard may still be starting">Open Ray Dashboard</a>'
        )
    )
    dash_status = (
        '<span class="phase-ok">ready</span>'
        if dash_ok
        else '<span class="phase-busy">starting…</span>'
    )
    op_html = ""
    if op and op.running:
        op_html = (
            f'<div class="op-banner active" id="op-banner">'
            f"Background operation running: <strong>{op.kind}</strong> "
            f"(started {op.started_at}). This page refreshes automatically."
            f"</div>"
        )
    elif op and op.error:
        op_html = (
            f'<div class="op-banner active" id="op-banner">'
            f'Last operation <strong>{op.kind}</strong> failed: {op.error}'
            f"</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_tab_title()}</title>
<style>{PAGE_STYLE}</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="brand">
      <h1>CANFAR Ray Manager</h1>
      <p>Launch and manage CANFAR worker sessions · monitor jobs in Ray Dashboard</p>
    </div>
    <div class="cta-row">
      {dash_cta}
      <a class="btn" href="{p_status}" target="_blank">JSON status</a>
    </div>
  </div>
  {flash_html(flash, flash_msg)}
  {op_html}
  <div class="cards">
    <div class="card">
      <div class="label">Cluster phase</div>
      <div class="value {phase_class(cluster_phase)}" id="cluster-phase">{cluster_phase}</div>
      <div class="sub">id {_settings.cluster_id}</div>
    </div>
    <div class="card">
      <div class="label">Workers joined</div>
      <div class="value" id="workers-joined">{joined}/{target or "—"}</div>
      <div class="progress"><span id="join-bar" style="width:{progress_pct}%"></span></div>
    </div>
    <div class="card">
      <div class="label">Ray nodes</div>
      <div class="value" id="ray-nodes">{live_nodes}</div>
      <div class="sub">{ray_address()}</div>
    </div>
    <div class="card">
      <div class="label">Ray Dashboard</div>
      <div class="value" id="dash-status">{dash_status}</div>
      <div class="sub">proxied at {p_dash}</div>
    </div>
  </div>
  <div class="panel">
    <h2>Setup checklist</h2>
    {checklist_html}
    <p class="muted">Use Ray Dashboard for jobs, actors, logs, and metrics. This page controls CANFAR worker sessions.</p>
  </div>
  <div class="panel">
    <h2>Create cluster</h2>
    <form method="post" action="{p_create}" id="create-cluster-form" onsubmit="if(!window.__setupReady){{return false;}} var b=this.querySelector('button[type=submit]'); b.disabled=true; b.textContent='Creating...';">
      <fieldset id="create-cluster-fieldset"{create_disabled}>
      <div class="grid">
        <label>Workers <input name="worker_count" type="number" value="2" min="1" max="16" required></label>
        <label>CPUs/worker <input name="cores" type="number" value="1" min="1" required></label>
        <label>RAM GB/worker <input name="ram_gb" type="number" value="4" min="1" required></label>
        <label>GPUs/worker <input name="gpus" type="number" value="0" min="0" max="8" required></label>
        <label>Min joined <input name="min_joined" type="number" value="2" min="1" required></label>
        <label>Partial policy
          <select name="partial_policy" required>
            <option value="accept_partial">accept partial</option>
            <option value="fail_and_cleanup">fail and cleanup</option>
            <option value="continue_waiting">continue waiting</option>
          </select>
        </label>
      </div>
      <button class="btn btn-primary" type="submit" id="create-cluster-btn">Create cluster</button>
      </fieldset>
    </form>
  </div>
  <div class="panel">
    <h2>Maintenance</h2>
    <div class="actions">
      <form method="post" action="{p_preflight}" onsubmit="var b=this.querySelector('button[type=submit]'); b.disabled=true; b.textContent='Running...';"><button class="btn" type="submit">Run network preflight</button></form>
      <form method="post" action="{p_reconcile}" onsubmit="var b=this.querySelector('button[type=submit]'); b.disabled=true; b.textContent='Reconciling...';"><button class="btn" type="submit">Reconcile state</button></form>
      <form method="post" action="{p_stop}" onsubmit="if(!confirm('Stop the cluster and terminate all workers?')) return false; var b=this.querySelector('button[type=submit]'); b.disabled=true; b.textContent='Stopping...';"><button class="btn btn-danger" type="submit">Stop cluster</button></form>
      <form method="post" action="{p_orphans}" onsubmit="if(!confirm('Clean orphaned worker sessions?')) return false; var b=this.querySelector('button[type=submit]'); b.disabled=true; b.textContent='Cleaning...';"><button class="btn" type="submit">Clean orphaned workers</button></form>
    </div>
  </div>
  <div class="panel">
    <h2>Workers</h2>
    <div id="workers-panel">{workers_html or '<p class="muted" id="workers-empty">No workers recorded.</p>'}</div>
  </div>
  <p class="footer">
    <a href="{p_dash}">Ray Dashboard</a> ·
    <a href="{p_auth}">Auth status</a> ·
    <a href="{p_health}">healthz</a> ·
    Ray {_settings.ray_version}
  </p>
</div>
<script>
(function () {{
  window.__setupReady = {"true" if cluster_ready else "false"};
  const uiPaths = {{
    retryWorkerPrefix: {ui_paths["retry_worker_prefix"]!r},
    workerLogsPrefix: {ui_paths["worker_logs_prefix"]!r},
    preflightAction: {ui_paths["preflight_action"]!r},
  }};
  const phaseEl = document.getElementById("cluster-phase");
  const joinedEl = document.getElementById("workers-joined");
  const nodesEl = document.getElementById("ray-nodes");
  const dashEl = document.getElementById("dash-status");
  const barEl = document.getElementById("join-bar");
  const opEl = document.getElementById("op-banner");
  const workersPanel = document.getElementById("workers-panel");
  const createFieldset = document.getElementById("create-cluster-fieldset");
  const setupBanner = document.getElementById("setup-ready-banner");
  const checklistAuth = document.getElementById("checklist-auth");
  const checklistPreflight = document.getElementById("checklist-preflight");
  const authStatusEl = document.getElementById("auth-status");
  const preflightStatusEl = document.getElementById("preflight-status");

  function esc(text) {{
    return String(text ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }}

  function renderWorkersTable(entries) {{
    if (!entries || !entries.length) {{
      return '<p class="muted" id="workers-empty">No workers recorded.</p>';
    }}
    let rows = entries.map(function (entry) {{
      let notes = esc(entry.last_error);
      if (entry.logs_available) {{
        const logsHref = esc(uiPaths.workerLogsPrefix + entry.session_id + "/logs");
        notes += ' <a href="' + logsHref + '" target="_blank" rel="noopener noreferrer" aria-label="View logs for worker ' + esc(entry.name) + '">logs</a>';
      }}
      if (entry.retry_available) {{
        const retryAction = esc(uiPaths.retryWorkerPrefix + entry.session_id);
        notes += ' <form class="inline" method="post" action="' + retryAction + '" onsubmit="var b=this.querySelector(\\'button[type=submit]\\'); b.disabled=true; b.textContent=\\'Retrying...\\';"><button class="btn btn-ghost" type="submit" aria-label="Retry worker ' + esc(entry.name) + '">Retry</button></form>';
      }}
      return "<tr><td>" + esc(entry.name) + "</td><td><code>" + esc(entry.session_id) + "</code></td>"
        + '<td class="' + esc(entry.phase_class) + '">' + esc(entry.phase) + "</td>"
        + "<td>" + esc(entry.canfar_status) + "</td>"
        + '<td class="mono">' + esc(entry.worker_ip) + "</td>"
        + "<td>" + esc(entry.joined_label) + "</td>"
        + "<td>" + notes + "</td></tr>";
    }}).join("");
    return '<table id="workers-table"><tr><th>Name</th><th>Session</th><th>Phase</th><th>CANFAR</th>'
      + "<th>IP</th><th>Ray</th><th>Notes</th></tr>" + rows + "</table>";
  }}

  function renderAuthDetail(auth) {{
    if (auth.authenticated) {{
      return '<span class="phase-ok">Authenticated (' + esc(auth.idp || "ok") + ")</span>";
    }}
    return '<span class="phase-bad">Not authenticated</span> — run in an AstroAI <strong>terminal</strong> or <strong>vscode</strong> session: <code>canfar auth login</code> <button type="button" class="btn btn-ghost btn-sm" id="copy-auth-cmd">Copy</button>';
  }}

  function renderPreflightDetail(preflight, paths) {{
    const pf = preflight || {{}};
    if (pf.passed) {{
      return '<span class="phase-ok">Passed</span> (probe ' + esc(pf.worker_ip || "?") + ")";
    }}
    let html = '<span class="phase-warn">Not run or failed</span> — verifies pod-to-pod networking before workers launch.';
    html += ' <form class="inline checklist-action" method="post" action="' + esc(paths.preflightAction) + '" onsubmit="var b=this.querySelector(\\'button[type=submit]\\'); b.disabled=true; b.textContent=\\'Running...\\';"><button class="btn btn-sm" type="submit">Run network preflight</button></form>';
    return html;
  }}

  function bindCopyAuth() {{
    const btn = document.getElementById("copy-auth-cmd");
    if (!btn || btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", function () {{
      navigator.clipboard.writeText("canfar auth login").then(function () {{
        btn.textContent = "Copied!";
        setTimeout(function () {{ btn.textContent = "Copy"; }}, 1500);
      }}).catch(function () {{}});
    }});
  }}

  function updateSetupChecklist(status) {{
    const auth = status.auth || {{}};
    const pf = status.preflight || {{}};
    const ready = !!status.setup_ready;
    window.__setupReady = ready;
    if (checklistAuth) checklistAuth.className = auth.authenticated ? "checklist-done" : "checklist-todo";
    if (checklistPreflight) checklistPreflight.className = pf.passed ? "checklist-done" : "checklist-todo";
    if (authStatusEl) authStatusEl.innerHTML = renderAuthDetail(auth);
    if (preflightStatusEl) preflightStatusEl.innerHTML = renderPreflightDetail(pf, status.ui_paths || uiPaths);
    if (setupBanner) {{
      setupBanner.className = ready ? "checklist-ready" : "checklist-blocked";
      setupBanner.textContent = ready
        ? "Setup complete — you can create a cluster."
        : "Complete the checklist above before creating a cluster.";
    }}
    if (createFieldset) createFieldset.disabled = !ready;
    bindCopyAuth();
  }}

  async function refresh() {{
    try {{
      const [status, dash] = await Promise.all([
        fetch("{p_status}").then(r => r.json()),
        fetch("{p_dash_status}").then(r => r.json()),
      ]);
      const cluster = status.cluster || {{}};
      const phase = cluster.phase || "Idle";
      const joined = status.joined_workers || 0;
      const target = cluster.worker_count || 0;
      if (phaseEl) {{
        phaseEl.textContent = phase;
        phaseEl.className = "value";
      }}
      if (joinedEl) joinedEl.textContent = target ? (joined + "/" + target) : (joined + "/—");
      if (nodesEl) nodesEl.textContent = status.ray_nodes_alive ?? "—";
      if (barEl) barEl.style.width = (target ? Math.min(100, Math.round(100 * joined / target)) : 0) + "%";
      if (dashEl) dashEl.innerHTML = dash.ready
        ? '<span class="phase-ok">ready</span>'
        : '<span class="phase-busy">starting…</span>';
      if (workersPanel) workersPanel.innerHTML = renderWorkersTable(status.worker_ui || []);
      updateSetupChecklist(status);
      if (opEl) {{
        const op = status.operation;
        if (op && op.running) {{
          opEl.classList.add("active");
          opEl.innerHTML = "Background operation running: <strong>" + op.kind +
            "</strong> (started " + op.started_at + "). This page refreshes automatically.";
        }} else if (op && op.error) {{
          opEl.classList.add("active");
          opEl.innerHTML = "Last operation <strong>" + op.kind + "</strong> failed: " + op.error;
        }} else if (opEl.dataset.keep !== "1") {{
          opEl.classList.remove("active");
        }}
      }}
    }} catch (e) {{ /* ignore transient poll errors */ }}
  }}
  bindCopyAuth();
  setInterval(refresh, 4000);
}})();
</script>
</body></html>"""


def _worker_ui_entries(state: ClusterState | None) -> list[dict[str, Any]]:
    if not state:
        return []
    entries: list[dict[str, Any]] = []
    for worker in state.workers:
        entries.append(
            {
                "name": worker.name,
                "session_id": worker.session_id,
                "phase": worker.phase,
                "phase_class": phase_class(worker.phase),
                "canfar_status": worker.canfar_status or "—",
                "worker_ip": worker.worker_ip or "—",
                "ray_joined": worker.ray_joined,
                "joined_label": "joined" if worker.ray_joined else "pending",
                "last_error": worker.last_error or "",
                "logs_available": bool(
                    worker.logs_path or _store.worker_log_file(worker.session_id).is_file()
                ),
                "retry_available": worker.phase in RETRY_PHASES,
            }
        )
    return entries


def _ui_paths() -> dict[str, str]:
    return {
        "retry_worker_prefix": public_path("/actions/retry-worker/"),
        "worker_logs_prefix": public_path("/api/v1/workers/"),
        "preflight_action": public_path("/actions/preflight"),
    }


def _cluster_payload(state: Any, nodes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    op = active_operation()
    if nodes is None:
        nodes = list_ray_nodes()
    auth = _canfar.auth_status()
    preflight = state.preflight if state else None
    payload = {
        "ray_address": ray_address(),
        "manager_ip": manager_pod_ip(),
        "cluster_setup_ready": state.setup_ready if state else False,
        "setup_ready_seconds": state.setup_ready_seconds if state else None,
        "ray_version": _settings.ray_version,
        "cluster_id": _settings.cluster_id,
        "heartbeat_path": str(_heartbeat_path()),
        "ray_running": ray_running(),
        "ray_nodes_alive": count_live_nodes(nodes=nodes),
        "dashboard_ready": dashboard_ready(),
        "dashboard_path": public_path("/dashboard/"),
        "worker_image": _settings.worker_image,
        "cluster": asdict(state) if state else None,
        "preflight": preflight,
        "workers": [asdict(w) for w in state.workers] if state else [],
        "worker_ui": _worker_ui_entries(state),
        "ui_paths": _ui_paths(),
        "auth": asdict(auth),
        "setup_ready": setup_ready(authenticated=auth.authenticated, preflight=preflight),
        "joined_workers": len(_store.joined_workers(state)) if state else 0,
        "operation": op.as_dict() if op else None,
    }
    if state and state.phase not in {"Creating", "Idle"}:
        joined = payload["joined_workers"]
        target = state.worker_count or len(state.workers)
        if joined >= target and target > 0:
            payload["success"] = True
        elif joined >= state.min_joined and joined > 0:
            payload["success"] = True
            payload["message"] = f"Partial cluster: {joined}/{target} workers joined"
        elif state.phase in {"Failed", "Stopped"}:
            payload["success"] = False
    return payload
