"""Best-effort: default OpenResearch compute to CANFAR batch (Ray under the hood).

Called from openresearch startup. Never fails the session.
- If a manager Jobs URL is already known (ASTROAI_RAY_JOBS_ADDRESS), wire orx.
- Else discover a Running/Pending ray-manager session via `canfar ps --json`
  and derive the Jobs URL from its connect URL (or a persisted connect URL).
- Never set defaultBackend=ray without an address — orx would fall through to
  localhost:8265 and confuse first-run users.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _run(cmd: list[str], *, timeout: int) -> tuple[int, str, str]:
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


def _parse_json_blob(text: str) -> Any | None:
    raw = (text or "").strip()
    if not raw:
        return None
    for marker in ("[", "{"):
        idx = raw.find(marker)
        if idx >= 0:
            raw = raw[idx:]
            break
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _orx_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg) / "openresearch"
    return Path.home() / ".config" / "openresearch"


def canfar_sessions(*, timeout: int = 30) -> list[dict[str, Any]]:
    if shutil.which("canfar") is None:
        return []
    rc, out, _err = _run(["canfar", "ps", "--json"], timeout=timeout)
    if rc != 0:
        return []
    data = _parse_json_blob(out)
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


def _session_status(row: dict[str, Any]) -> str:
    return str(row.get("status") or "")


def _session_image(row: dict[str, Any]) -> str:
    return str(row.get("image") or row.get("imageName") or "")


def _session_name(row: dict[str, Any]) -> str:
    return str(row.get("name") or "")


def _session_connect_url(row: dict[str, Any]) -> str:
    url = str(row.get("connectURL") or row.get("connectUrl") or "").strip()
    if url and not url.endswith("/"):
        url += "/"
    return url


def find_manager_sessions(sessions: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = sessions if sessions is not None else canfar_sessions()
    out: list[dict[str, Any]] = []
    for row in rows:
        status = _session_status(row)
        if status not in {"Running", "Pending"}:
            continue
        image = _session_image(row).lower()
        name = _session_name(row).lower()
        if "ray-manager" in image or name in {
            "raymgr",
            "orx-ray-stg",
            "ray-manager",
            "astroai-compute",
        }:
            out.append(row)
    return out


def jobs_url_from_connect(connect_url: str) -> str:
    base = connect_url.rstrip("/") + "/"
    # Dashboard reverse-proxy exposes Jobs API under /dashboard/
    return base + "dashboard"


def read_persisted_connect_url() -> str | None:
    clusters = Path.home() / ".astroai" / "ray" / "clusters"
    if not clusters.is_dir():
        return None
    candidates: list[tuple[float, str]] = []
    for root in clusters.iterdir():
        if not root.is_dir():
            continue
        path = root / "connect-url"
        if not path.is_file():
            continue
        try:
            url = path.read_text(encoding="utf-8").strip()
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if url:
            candidates.append((mtime, url if url.endswith("/") else url + "/"))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def wire_orx(*, jobs_address: str, make_default: bool = True) -> dict[str, Any]:
    """Write OpenResearch Ray settings + optional default compute target."""
    cfg = _orx_config_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    address = jobs_address.rstrip("/")
    ray_path = cfg / "ray.json"
    ray_path.write_text(json.dumps({"address": address}, indent=2) + "\n", encoding="utf-8")
    settings_path = cfg / "settings.json"
    settings: dict[str, Any] = {}
    if settings_path.is_file():
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                settings = loaded
        except (OSError, json.JSONDecodeError):
            settings = {}
    if make_default:
        settings["defaultBackend"] = "ray"
        settings.pop("defaultFlavor", None)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    os.environ["ASTROAI_RAY_JOBS_ADDRESS"] = address
    return {
        "ray_json": str(ray_path),
        "settings_json": str(settings_path),
        "address": address,
        "default_backend": settings.get("defaultBackend"),
    }


def main() -> int:
    try:
        jobs = (os.environ.get("ASTROAI_RAY_JOBS_ADDRESS") or "").strip().rstrip("/")
        if not jobs:
            # Boot is discovery-only (persisted connect URL → canfar ps):
            # never create a manager as a side effect of starting a session.
            # The AstroAI hub **Start batch compute** button creates one.
            connect = read_persisted_connect_url()
            if not connect:
                managers = find_manager_sessions()
                running = [
                    m
                    for m in managers
                    if _session_status(m) == "Running" and _session_connect_url(m)
                ]
                if running:
                    connect = _session_connect_url(running[0])
            if connect:
                jobs = jobs_url_from_connect(connect)

        if jobs:
            wire_orx(jobs_address=jobs, make_default=True)
    except Exception as exc:  # noqa: BLE001 — best-effort, never fail the session
        sys.stderr.write(f"orx-wire-compute: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
