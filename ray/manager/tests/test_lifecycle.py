"""Unit tests for Ray manager cluster lifecycle reliability helpers."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Stub heavy/optional deps so tests run without Ray or the canfar client installed.
_canfar = types.ModuleType("canfar")
_canfar_models = types.ModuleType("canfar.models")
_canfar_config = types.ModuleType("canfar.models.config")
_canfar_sessions = types.ModuleType("canfar.sessions")


class _Configuration:  # noqa: D101
    def __init__(self) -> None:
        self.active = MagicMock(authentication=None, server=None)
        self.registry = MagicMock(username=None, secret=None, url=None)

    def get_credential(self, _idp: str) -> None:
        raise KeyError(_idp)


class _Session:  # noqa: D101
    def __init__(self) -> None:
        self.config = MagicMock(registry=MagicMock(username=None, secret=None))

    def fetch(self, **_kwargs):  # noqa: ANN003
        return []

    def create(self, **_kwargs):  # noqa: ANN003
        return []

    def info(self, *_a, **_k):  # noqa: ANN003
        return []

    def logs(self, *_a, **_k):  # noqa: ANN003
        return {}

    def destroy(self, *_a, **_k):  # noqa: ANN003
        return {}


_canfar_config.Configuration = _Configuration
_canfar_sessions.Session = _Session
sys.modules.setdefault("canfar", _canfar)
sys.modules.setdefault("canfar.models", _canfar_models)
sys.modules.setdefault("canfar.models.config", _canfar_config)
sys.modules.setdefault("canfar.sessions", _canfar_sessions)
sys.modules.setdefault("ray", MagicMock(__version__="2.56.0"))

MANAGER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MANAGER_DIR))

from canfar_workload.cluster import (  # noqa: E402
    ClusterCreateRequest,
    clean_orphaned_workers,
    fail_create_cleanup,
    prepare_cluster_create,
    validate_cluster_create,
)
from canfar_workload.settings import ManagerSettings  # noqa: E402
from canfar_workload.state_store import ClusterState, StateStore, WorkerRecord  # noqa: E402


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StateStore:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("RAY_CLUSTER_ID", "testcid")
    s = StateStore(cluster_id="testcid")
    s.ensure_dir()
    return s


@pytest.fixture()
def settings() -> ManagerSettings:
    return ManagerSettings(
        cluster_id="testcid",
        worker_image="images.canfar.net/astroai/ray-worker:local",
        probe_image="images.canfar.net/astroai/ray-worker:local",
        ray_version="2.56.0",
        scratch_dir="/scratch",
        ray_head_port=6379,
        heartbeat_timeout_seconds=60,
        worker_launch_timeout_seconds=30,
        preflight_timeout_seconds=60,
    )


def _auth_ok(canfar: MagicMock) -> None:
    status = MagicMock()
    status.authenticated = True
    status.message = None
    canfar.auth_status.return_value = status


def _state(**kwargs):
    kwargs.setdefault("cluster_id", "testcid")
    kwargs.setdefault("manager_ip", "10.0.0.1")
    kwargs.setdefault("ray_address", "10.0.0.1:6379")
    return ClusterState(**kwargs)


def test_validate_rejects_stale_preflight_ip(store: StateStore, monkeypatch: pytest.MonkeyPatch) -> None:
    canfar = MagicMock()
    _auth_ok(canfar)
    state = _state(
        phase="Failed",
        preflight={"passed": True, "manager_ip": "10.0.0.1"},
    )
    store.save(state)
    monkeypatch.setattr("canfar_workload.cluster.manager_pod_ip", lambda: "10.0.0.99")
    req = ClusterCreateRequest(name="x", require_preflight=True)
    with pytest.raises(RuntimeError, match="stale"):
        validate_cluster_create(canfar=canfar, store=store, req=req)


def test_validate_accepts_matching_preflight_ip(store: StateStore, monkeypatch: pytest.MonkeyPatch) -> None:
    canfar = MagicMock()
    _auth_ok(canfar)
    store.save(
        _state(
            phase="Failed",
            preflight={"passed": True, "manager_ip": "10.0.0.5"},
        )
    )
    monkeypatch.setattr("canfar_workload.cluster.manager_pod_ip", lambda: "10.0.0.5")
    validate_cluster_create(
        canfar=canfar,
        store=store,
        req=ClusterCreateRequest(name="x", require_preflight=True),
    )


def test_prepare_destroys_tracked_workers_before_recreate(
    store: StateStore, settings: ManagerSettings
) -> None:
    canfar = MagicMock()
    canfar.list_headless_sessions.return_value = []
    canfar.destroy.return_value = True
    store.save(
        _state(
            phase="Failed",
            workers=[
                WorkerRecord(session_id="old1", name="ray-w-testcid-1", phase="Stopped"),
                WorkerRecord(session_id="old2", name="ray-w-testcid-2", phase="Stopped"),
            ],
        )
    )
    with patch("canfar_workload.cluster.archive_session_logs"), patch("canfar_workload.workers.archive_session_logs"):
        prepare_cluster_create(settings=settings, canfar=canfar, store=store)
    destroyed_ids = {c.args[0] for c in canfar.destroy.call_args_list}
    assert "old1" in destroyed_ids
    assert "old2" in destroyed_ids


def test_fail_create_cleanup_sets_failed(
    store: StateStore, settings: ManagerSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    canfar = MagicMock()
    canfar.list_headless_sessions.return_value = []
    canfar.destroy.return_value = True
    monkeypatch.setattr("canfar_workload.cluster.manager_pod_ip", lambda: "10.1.1.1")
    monkeypatch.setattr("canfar_workload.cluster.ray_address", lambda: "10.1.1.1:6379")
    store.save(
        _state(
            phase="Creating",
            workers=[WorkerRecord(session_id="w1", name="ray-w-1", phase="CANFAR Pending")],
        )
    )
    with patch("canfar_workload.cluster.archive_session_logs"), patch("canfar_workload.workers.archive_session_logs"):
        result = fail_create_cleanup(
            settings=settings, canfar=canfar, store=store, message="boom"
        )
    assert result.success is False
    assert result.state.phase == "Failed"
    canfar.destroy.assert_called()


def test_clean_orphans_destroys_preflight_when_idle(
    store: StateStore, settings: ManagerSettings
) -> None:
    canfar = MagicMock()
    store.save(_state(phase="Failed", workers=[]))

    def list_sessions(name_prefix: str):
        if name_prefix.startswith("ray-preflight"):
            return [{"id": "pf1", "name": f"ray-preflight-{settings.cluster_id}-x"}]
        return []

    canfar.list_headless_sessions.side_effect = list_sessions
    canfar.destroy.return_value = True
    destroyed = clean_orphaned_workers(settings=settings, canfar=canfar, store=store)
    assert any(d["session_id"] == "pf1" for d in destroyed)


def test_clean_orphans_destroys_tracked_when_terminal(
    store: StateStore, settings: ManagerSettings
) -> None:
    canfar = MagicMock()
    canfar.list_headless_sessions.return_value = []
    canfar.destroy.return_value = True
    store.save(
        _state(
            phase="Stopped",
            workers=[
                WorkerRecord(
                    session_id="ghost",
                    name="ray-w-testcid-ghost",
                    phase="Stopped",
                    canfar_status="Running",
                )
            ],
        )
    )
    destroyed = clean_orphaned_workers(settings=settings, canfar=canfar, store=store)
    assert any(d.get("session_id") == "ghost" for d in destroyed)
