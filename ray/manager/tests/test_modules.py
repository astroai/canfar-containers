"""Unit tests for Ray manager modules: canfar_ops, workers, reconcile, cluster."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------
# Stub heavy/optional deps so tests run without Ray or canfar client installed.
# ---------------------------------------------------------------
_canfar = types.ModuleType("canfar")
_canfar_models = types.ModuleType("canfar.models")
_canfar_config = types.ModuleType("canfar.models.config")
_canfar_sessions = types.ModuleType("canfar.sessions")


class _Configuration:
    def __init__(self) -> None:
        self.active = MagicMock(authentication=None, server=None)
        self.registry = MagicMock(username=None, secret=None, url=None)

    def get_credential(self, _idp: str) -> None:
        raise KeyError(_idp)


class _Session:
    def __init__(self) -> None:
        self.config = MagicMock(registry=MagicMock(username=None, secret=None))

    def fetch(self, **_kwargs):
        return []

    def create(self, **_kwargs):
        return []

    def info(self, *_a, **_k):
        return []

    def logs(self, *_a, **_k):
        return {}

    def destroy(self, *_a, **_k):
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

from astroai_workload.canfar_ops import (  # noqa: E402
    CanfarOps,
    SessionLaunch,
    parse_probe_logs,
)
from astroai_workload.cluster import (  # noqa: E402
    ClusterCreateRequest,
    gc_terminal_cluster_workers,
    retry_worker,
    stop_cluster,
    validate_cluster_create,
)
from astroai_workload.reconcile import (  # noqa: E402
    _apply_canfar_phase,
    _refresh_cluster_phase,
    enrich_worker_failure,
    reconcile_cluster,
)
from astroai_workload.settings import ManagerSettings  # noqa: E402
from astroai_workload.state_store import (  # noqa: E402
    ClusterState,
    StateStore,
    WorkerRecord,
)
from astroai_workload.workers import (  # noqa: E402
    build_worker_env,
    destroy_all_workers,
    destroy_worker,
)


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------
@pytest.fixture(autouse=True)
def _default_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAY_NODE_IP_ADDRESS", "127.0.0.1")


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
        heartbeat_timeout_seconds=120,
        worker_launch_timeout_seconds=30,
        preflight_timeout_seconds=60,
    )


def _state(**kwargs):
    kwargs.setdefault("cluster_id", "testcid")
    kwargs.setdefault("manager_ip", "10.0.0.1")
    kwargs.setdefault("ray_address", "10.0.0.1:6379")
    return ClusterState(**kwargs)


def _auth_ok(canfar: MagicMock) -> None:
    status = MagicMock()
    status.authenticated = True
    status.message = None
    canfar.auth_status.return_value = status


def _auth_bad(canfar: MagicMock, message: str = "not authed") -> None:
    status = MagicMock()
    status.authenticated = False
    status.message = message
    canfar.auth_status.return_value = status


# ===============================================================
# canfar_ops.py tests
# ===============================================================
class TestParseProbeLogs:
    def test_pass(self) -> None:
        logs = (
            "WORKER_IP=10.0.0.5\n"
            "PROBE worker->manager:6379 PASS\n"
            "PROBE worker->manager:6380 PASS\n"
            "PROBE_RESULT PASS\n"
        )
        result = parse_probe_logs(logs)
        assert result["worker_ip"] == "10.0.0.5"
        assert result["result"] == "PASS"
        assert len(result["checks"]) == 2
        assert result["checks"][0] == {"port": "6379", "result": "PASS"}

    def test_fail(self) -> None:
        logs = (
            "WORKER_IP=10.0.0.5\n"
            "PROBE worker->manager:6379 FAIL\n"
            "PROBE_RESULT FAIL\n"
        )
        result = parse_probe_logs(logs)
        assert result["result"] == "FAIL"
        assert result["checks"][0]["result"] == "FAIL"

    def test_empty_logs(self) -> None:
        result = parse_probe_logs("")
        assert result["worker_ip"] is None
        assert result["result"] == "UNKNOWN"
        assert result["checks"] == []

    def test_no_worker_ip(self) -> None:
        result = parse_probe_logs("PROBE_RESULT PASS\n")
        assert result["worker_ip"] is None
        assert result["result"] == "PASS"

    def test_multiple_ports(self) -> None:
        logs = "\n".join(
            f"PROBE worker->manager:{p} PASS" for p in ("6379", "6380", "6381", "6382", "6383")
        )
        result = parse_probe_logs(logs)
        assert len(result["checks"]) == 5


class TestAuthStatus:
    def test_authenticated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ops = CanfarOps()
        with patch.object(ops, "_fresh_session") as mock_fresh:
            mock_fresh.return_value.fetch.return_value = [{"id": "s1"}]
            with patch("astroai_workload.canfar_ops.Configuration") as MockConfig:
                cfg = MagicMock()
                cfg.active.authentication = "cadc"
                cfg.active.server = "https://example.com"
                cfg.get_credential.return_value = "ok"
                MockConfig.return_value = cfg

                result = ops.auth_status()
                assert result.authenticated is True
                assert result.idp == "cadc"

    def test_no_authentication_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ops = CanfarOps()
        with patch("astroai_workload.canfar_ops.Configuration") as MockConfig:
            cfg = MagicMock()
            cfg.active.authentication = None
            cfg.active.server = None
            MockConfig.return_value = cfg

            result = ops.auth_status()
            assert result.authenticated is False
            assert "No CANFAR authentication configured" in result.message

    def test_no_saved_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ops = CanfarOps()
        with patch("astroai_workload.canfar_ops.Configuration") as MockConfig:
            cfg = MagicMock()
            cfg.active.authentication = "cadc"
            cfg.active.server = "https://example.com"
            cfg.get_credential.side_effect = KeyError("cadc")
            MockConfig.return_value = cfg

            result = ops.auth_status()
            assert result.authenticated is False
            assert "No saved credentials" in result.message

    def test_session_fetch_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ops = CanfarOps()
        with patch("astroai_workload.canfar_ops.Configuration") as MockConfig:
            cfg = MagicMock()
            cfg.active.authentication = "cadc"
            cfg.active.server = "https://example.com"
            cfg.get_credential.return_value = "ok"
            MockConfig.return_value = cfg
            with patch.object(ops, "_fresh_session") as mock_fresh:
                mock_fresh.return_value.fetch.side_effect = RuntimeError("connection refused")
                result = ops.auth_status()
                assert result.authenticated is False
                assert "connection refused" in result.message


class TestCanfarOpsCreateHeadless:
    def test_success_single_replica(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ops = CanfarOps()
        with patch("astroai_workload.canfar_ops._registry_configured", return_value=True):
            with patch("astroai_workload.canfar_ops._registry_env", return_value={"REG": "val"}):
                with patch.object(ops, "_fresh_session") as mock_new:
                    mock_sess = MagicMock()
                    mock_sess.config.registry = MagicMock(username="u", secret="s")
                    mock_sess.create.return_value = ["sid-1"]
                    mock_new.return_value = mock_sess

                    results = ops.create_headless(
                        name="ray-w-test",
                        image="images.canfar.net/astroai/ray-worker:local",
                        cores=2,
                        ram=8,
                        gpu=1,
                        replicas=1,
                    )
                    assert len(results) == 1
                    assert results[0].session_id == "sid-1"
                    assert results[0].name == "ray-w-test"

    def test_multiple_replicas(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ops = CanfarOps()
        with patch("astroai_workload.canfar_ops._registry_configured", return_value=True):
            with patch("astroai_workload.canfar_ops._registry_env", return_value={}):
                with patch.object(ops, "_fresh_session") as mock_new:
                    mock_sess = MagicMock()
                    mock_sess.create.return_value = ["sid-1", "sid-2", "sid-3"]
                    mock_new.return_value = mock_sess

                    results = ops.create_headless(
                        name="ray-w-test",
                        image="images.canfar.net/astroai/ray-worker:local",
                        replicas=3,
                    )
                    assert len(results) == 3
                    assert results[0].name == "ray-w-test-1"
                    assert results[1].name == "ray-w-test-2"
                    assert results[2].name == "ray-w-test-3"

    def test_no_registry_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ops = CanfarOps()
        with patch("astroai_workload.canfar_ops._registry_configured", return_value=False):
            with pytest.raises(RuntimeError, match="Harbor registry credentials"):
                ops.create_headless(
                    name="x",
                    image="img",
                )

    def test_create_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ops = CanfarOps()
        with patch("astroai_workload.canfar_ops._registry_configured", return_value=True):
            with patch("astroai_workload.canfar_ops._registry_env", return_value={}):
                with patch.object(ops, "_fresh_session") as mock_new:
                    mock_sess = MagicMock()
                    mock_sess.create.return_value = []
                    mock_new.return_value = mock_sess
                    with patch.object(ops, "_resolve_ids_by_name", return_value=[]):
                        with pytest.raises(RuntimeError, match="no session ID"):
                            ops.create_headless(name="x", image="img")

    def test_create_empty_recovers_via_name_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ops = CanfarOps()
        with patch("astroai_workload.canfar_ops._registry_configured", return_value=True):
            with patch("astroai_workload.canfar_ops._registry_env", return_value={}):
                with patch.object(ops, "_fresh_session") as mock_new:
                    mock_sess = MagicMock()
                    mock_sess.create.return_value = []
                    mock_new.return_value = mock_sess
                    with patch.object(
                        ops, "_resolve_ids_by_name", return_value=["recovered-sid"]
                    ) as probe:
                        results = ops.create_headless(name="ray-w-test", image="img")
                    assert len(results) == 1
                    assert results[0].session_id == "recovered-sid"
                    probe.assert_called_once_with(name="ray-w-test", replicas=1)

class TestCanfarOpsWaitForStatus:
    def test_reaches_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ops = CanfarOps()
        with patch.object(ops, "session_status", side_effect=["Pending", "Pending", "Running"]):
            result = ops.wait_for_status(
                "sid-1",
                target={"Running"},
                timeout_seconds=60,
                poll_seconds=1,
            )
            assert result == "Running"

    def test_hits_terminal_early(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ops = CanfarOps()
        with patch.object(ops, "session_status", side_effect=["Pending", "Failed"]):
            result = ops.wait_for_status(
                "sid-1",
                target={"Running"},
                timeout_seconds=60,
                poll_seconds=1,
            )
            assert result == "Failed"


class TestCanfarOpsSessionHelpers:
    def test_session_failure_detail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ops = CanfarOps()
        with patch.object(ops, "session_info", return_value={"statusMessage": "OOMKilled"}):
            detail = ops.session_failure_detail("sid-1")
            assert detail == "statusMessage=OOMKilled"

    def test_session_failure_detail_no_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ops = CanfarOps()
        with patch.object(ops, "session_info", return_value={}):
            detail = ops.session_failure_detail("sid-1")
            assert detail is None

    def test_session_logs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ops = CanfarOps()
        mock_sess = MagicMock()
        mock_sess.logs.return_value = {"sid-1": "line1\nline2\n"}
        ops._fresh_session = MagicMock(return_value=mock_sess)
        assert ops.session_logs("sid-1") == "line1\nline2\n"

    def test_session_logs_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ops = CanfarOps()
        mock_sess = MagicMock()
        mock_sess.logs.return_value = {}
        ops._fresh_session = MagicMock(return_value=mock_sess)
        assert ops.session_logs("sid-1") == ""


# ===============================================================
# workers.py tests
# ===============================================================
class TestBuildWorkerEnv:
    def test_basic_env(self, settings: ManagerSettings, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("astroai_workload.workers.manager_pod_ip", lambda: "10.0.0.1")
        env = build_worker_env(settings, "/arc/home/u/heartbeat")

        assert env["RAY_CLUSTER_ID"] == "testcid"
        assert env["RAY_HEAD_IP"] == "10.0.0.1"
        assert env["RAY_HEAD_PORT"] == "6379"
        assert env["RAY_VERSION_EXPECTED"] == "2.56.0"
        assert env["RAY_WORKER_CPUS"] == "1"
        assert env["RAY_WORKER_GPUS"] == "0"
        assert env["RAY_SPILL_DIR"] == "/scratch/ray/testcid"
        assert env["RAY_MANAGER_HEARTBEAT_PATH"] == "/arc/home/u/heartbeat"
        assert env["RAY_MANAGER_HEARTBEAT_TIMEOUT_SECONDS"] == "120"

    def test_optional_ray_ports(self, settings: ManagerSettings, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("astroai_workload.workers.manager_pod_ip", lambda: "10.0.0.1")
        monkeypatch.setenv("RAY_NODE_MANAGER_PORT", "7000")
        monkeypatch.setenv("RAY_DASHBOARD_AGENT_GRPC_PORT", "7001")

        env = build_worker_env(settings, "/heartbeat")
        assert env["RAY_NODE_MANAGER_PORT"] == "7000"
        assert env["RAY_DASHBOARD_AGENT_GRPC_PORT"] == "7001"
        assert "RAY_MIN_WORKER_PORT" not in env  # not set in env


class TestDestroyWorker:
    def test_destroys_and_archives(
        self, store: StateStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        canfar = MagicMock()
        canfar.destroy.return_value = True
        store.save(
            _state(
                phase="Running",
                workers=[WorkerRecord(session_id="w1", name="ray-w-1", phase="Ray Healthy")],
            )
        )
        with patch("astroai_workload.workers.archive_session_logs") as mock_archive:
            result = destroy_worker(canfar=canfar, store=store, session_id="w1")
            mock_archive.assert_called_once()
        assert result["destroyed"] is True
        canfar.destroy.assert_called_once_with("w1")

    def test_updates_phase_after_destroy(
        self, store: StateStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        canfar = MagicMock()
        canfar.destroy.return_value = True
        store.save(
            _state(
                phase="Running",
                workers=[WorkerRecord(session_id="w1", name="ray-w-1", phase="Ray Healthy")],
            )
        )
        with patch("astroai_workload.workers.archive_session_logs"):
            destroy_worker(canfar=canfar, store=store, session_id="w1")
        state = store.load()
        assert state is not None
        w = state.workers[0]
        assert w.phase == "Stopping"


class TestDestroyAllWorkers:
    def test_skips_stopped_by_default(
        self, store: StateStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        canfar = MagicMock()
        canfar.destroy.return_value = True
        store.save(
            _state(
                phase="Running",
                workers=[
                    WorkerRecord(session_id="active", name="w-active", phase="Ray Healthy"),
                    WorkerRecord(session_id="done", name="w-done", phase="Stopped"),
                ],
            )
        )
        with patch("astroai_workload.workers.archive_session_logs"):
            with patch("astroai_workload.workers.destroy_worker", wraps=destroy_worker) as mock_dw:
                results = destroy_all_workers(canfar=canfar, store=store)
        destroyed_ids = {r["session_id"] for r in results}
        assert "active" in destroyed_ids
        assert "done" not in destroyed_ids

    def test_include_terminal_destroys_all(
        self, store: StateStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        canfar = MagicMock()
        canfar.destroy.return_value = True
        store.save(
            _state(
                phase="Stopping",
                workers=[
                    WorkerRecord(session_id="active", name="w-active", phase="Ray Healthy"),
                    WorkerRecord(session_id="done", name="w-done", phase="Stopped"),
                ],
            )
        )
        with patch("astroai_workload.workers.archive_session_logs"):
            with patch("astroai_workload.workers.destroy_worker", wraps=destroy_worker) as mock_dw:
                results = destroy_all_workers(
                    canfar=canfar, store=store, include_terminal=True
                )
        destroyed_ids = {r["session_id"] for r in results}
        assert "active" in destroyed_ids
        assert "done" in destroyed_ids

    def test_empty_state_returns_empty(self, store: StateStore) -> None:
        canfar = MagicMock()
        results = destroy_all_workers(canfar=canfar, store=store)
        assert results == []


# ===============================================================
# reconcile.py tests
# ===============================================================
class TestApplyCanfarPhase:
    def test_pending(self) -> None:
        w = WorkerRecord(session_id="w1", name="w", phase="Requested", canfar_status="Pending")
        _apply_canfar_phase(w)
        assert w.phase == "CANFAR Pending"

    def test_running_not_joined(self) -> None:
        w = WorkerRecord(session_id="w1", name="w", phase="Requested", canfar_status="Running")
        w.ray_joined = False
        _apply_canfar_phase(w)
        assert w.phase == "Ray Joining"

    def test_running_already_healthy(self) -> None:
        w = WorkerRecord(
            session_id="w1", name="w", phase="Ray Healthy", canfar_status="Running"
        )
        w.ray_joined = True
        _apply_canfar_phase(w)
        assert w.phase == "Ray Healthy"  # unchanged

    def test_failed(self) -> None:
        w = WorkerRecord(session_id="w1", name="w", phase="Requested", canfar_status="Failed")
        _apply_canfar_phase(w)
        assert w.phase == "CANFAR Failed"
        assert "CANFAR status=Failed" in (w.last_error or "")

    def test_error(self) -> None:
        w = WorkerRecord(session_id="w1", name="w", phase="Requested", canfar_status="Error")
        _apply_canfar_phase(w)
        assert w.phase == "CANFAR Failed"

    def test_succeeded(self) -> None:
        w = WorkerRecord(session_id="w1", name="w", phase="Requested", canfar_status="Succeeded")
        _apply_canfar_phase(w)
        assert w.phase == "Stopped"

    def test_terminating(self) -> None:
        w = WorkerRecord(
            session_id="w1", name="w", phase="Requested", canfar_status="Terminating"
        )
        _apply_canfar_phase(w)
        assert w.phase == "Stopped"


class TestEnrichWorkerFailure:
    def test_adds_detail_for_failed(self) -> None:
        canfar = MagicMock()
        canfar.session_failure_detail.return_value = "exitCode=137"
        w = WorkerRecord(
            session_id="w1", name="w", phase="CANFAR Failed", last_error="CANFAR status=Failed"
        )
        enrich_worker_failure(canfar, w)
        assert "exitCode=137" in (w.last_error or "")

    def test_noop_when_not_failed(self) -> None:
        canfar = MagicMock()
        w = WorkerRecord(session_id="w1", name="w", phase="Ray Joining")
        enrich_worker_failure(canfar, w)
        assert w.last_error is None
        canfar.session_failure_detail.assert_not_called()

    def test_noop_when_no_detail(self) -> None:
        canfar = MagicMock()
        canfar.session_failure_detail.return_value = None
        w = WorkerRecord(
            session_id="w1", name="w", phase="CANFAR Failed", last_error="boom"
        )
        enrich_worker_failure(canfar, w)
        assert w.last_error == "boom"  # unchanged


class TestRefreshClusterPhase:
    def test_all_joined_running(self) -> None:
        from datetime import UTC, datetime, timedelta

        # Stabilization gate requires setup_ready for MIN_SETUP_STABLE_SECONDS.
        since = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
        state = _state(
            phase="Creating",
            worker_count=2,
            min_joined=2,
            setup_ready=True,
            setup_ready_since=since,
            workers=[
                WorkerRecord(session_id="w1", name="w1", phase="Ray Healthy", ray_joined=True),
                WorkerRecord(session_id="w2", name="w2", phase="Ray Healthy", ray_joined=True),
            ],
        )
        _refresh_cluster_phase(state)
        assert state.phase == "Running"

    def test_partial_degraded(self) -> None:
        state = _state(
            phase="Creating",
            worker_count=3,
            min_joined=1,
            workers=[
                WorkerRecord(session_id="w1", name="w1", phase="Ray Healthy", ray_joined=True),
                WorkerRecord(session_id="w2", name="w2", phase="Ray Joining"),
                WorkerRecord(session_id="w3", name="w3", phase="CANFAR Failed"),
            ],
        )
        _refresh_cluster_phase(state)
        assert state.phase == "Degraded"

    def test_all_failed(self) -> None:
        state = _state(
            phase="Creating",
            worker_count=2,
            workers=[
                WorkerRecord(session_id="w1", name="w1", phase="CANFAR Failed"),
                WorkerRecord(session_id="w2", name="w2", phase="Stopped"),
            ],
        )
        _refresh_cluster_phase(state)
        assert state.phase == "Failed"

    def test_stopping_with_active_stays_stopping(self) -> None:
        state = _state(
            phase="Stopping",
            worker_count=2,
            workers=[
                WorkerRecord(session_id="w1", name="w1", phase="Stopping"),
                WorkerRecord(session_id="w2", name="w2", phase="Ray Healthy", ray_joined=True),
            ],
        )
        _refresh_cluster_phase(state)
        assert state.phase == "Stopping"

    def test_stopping_all_terminal_becomes_stopped(self) -> None:
        state = _state(
            phase="Stopping",
            worker_count=2,
            workers=[
                WorkerRecord(session_id="w1", name="w1", phase="Stopped"),
                WorkerRecord(session_id="w2", name="w2", phase="Orphaned"),
            ],
        )
        _refresh_cluster_phase(state)
        assert state.phase == "Stopped"

    def test_active_phase_ignored(self) -> None:
        """Non-active terminal phases are not touched by _refresh_cluster_phase."""
        state = _state(phase="Stopped")
        _refresh_cluster_phase(state)
        assert state.phase == "Stopped"


class TestReconcileCluster:
    def test_updates_manager_ip(self, store: StateStore, monkeypatch: pytest.MonkeyPatch) -> None:
        canfar = MagicMock()
        _auth_ok(canfar)
        store.save(_state(phase="Running", manager_ip="10.0.0.99"))
        monkeypatch.setattr("astroai_workload.reconcile.manager_pod_ip", lambda: "10.0.0.1")
        monkeypatch.setattr("astroai_workload.reconcile.ray_address", lambda: "10.0.0.1:6379")
        monkeypatch.setattr("astroai_workload.reconcile.list_ray_nodes", lambda *a, **k: [])
        monkeypatch.setattr("astroai_workload.reconcile.live_worker_node_ips", lambda *a, **k: set())
        monkeypatch.setattr("astroai_workload.reconcile.node_ip_to_id", lambda *a, **k: {})

        result = reconcile_cluster(canfar=canfar, store=store)
        assert result is not None
        assert result.manager_ip == "10.0.0.1"

    def test_marks_workers_joined(self, store: StateStore, monkeypatch: pytest.MonkeyPatch) -> None:
        canfar = MagicMock()
        _auth_ok(canfar)
        canfar.session_info.return_value = {"status": "Running"}

        store.save(
            _state(
                phase="Creating",
                workers=[
                    WorkerRecord(
                        session_id="w1", name="w1", phase="CANFAR Pending", worker_ip="10.0.0.5"
                    ),
                ],
            )
        )
        monkeypatch.setattr("astroai_workload.reconcile.manager_pod_ip", lambda: "10.0.0.1")
        monkeypatch.setattr("astroai_workload.reconcile.ray_address", lambda: "10.0.0.1:6379")
        monkeypatch.setattr("astroai_workload.reconcile.list_ray_nodes", lambda *a, **k: [])
        monkeypatch.setattr("astroai_workload.reconcile.live_worker_node_ips", lambda *a, **k: {"10.0.0.5"})
        monkeypatch.setattr(
            "astroai_workload.reconcile.node_ip_to_id", lambda *a, **k: {"10.0.0.5": "node-1"}
        )

        with patch("astroai_workload.reconcile.archive_session_logs"):
            result = reconcile_cluster(canfar=canfar, store=store)
        assert result is not None
        w = result.workers[0]
        assert w.ray_joined is True
        assert w.ray_node_id == "node-1"
        assert w.phase == "Ray Healthy"

    def test_clears_outdated_preflight(self, store: StateStore, monkeypatch: pytest.MonkeyPatch) -> None:
        canfar = MagicMock()
        _auth_ok(canfar)
        store.save(
            _state(
                phase="Idle",
                preflight={"passed": True, "manager_ip": "10.0.0.88"},
            )
        )
        monkeypatch.setattr("astroai_workload.reconcile.manager_pod_ip", lambda: "10.0.0.1")
        monkeypatch.setattr("astroai_workload.reconcile.ray_address", lambda: "10.0.0.1:6379")
        monkeypatch.setattr("astroai_workload.reconcile.list_ray_nodes", lambda *a, **k: [])
        monkeypatch.setattr("astroai_workload.reconcile.live_worker_node_ips", lambda *a, **k: set())
        monkeypatch.setattr("astroai_workload.reconcile.node_ip_to_id", lambda *a, **k: {})

        result = reconcile_cluster(canfar=canfar, store=store)
        assert result is not None
        assert result.preflight is None

    def test_noop_when_no_state(self, store: StateStore) -> None:
        canfar = MagicMock()
        result = reconcile_cluster(canfar=canfar, store=store)
        assert result is None


# ===============================================================
# cluster.py tests (beyond test_lifecycle.py)
# ===============================================================
class TestValidateClusterCreate:
    def test_rejects_no_auth(self, store: StateStore) -> None:
        canfar = MagicMock()
        _auth_bad(canfar)
        req = ClusterCreateRequest(name="x")
        with pytest.raises(RuntimeError, match="not authed"):
            validate_cluster_create(canfar=canfar, store=store, req=req)

    def test_rejects_active_cluster(self, store: StateStore) -> None:
        canfar = MagicMock()
        _auth_ok(canfar)
        store.save(_state(phase="Running"))
        req = ClusterCreateRequest(name="x", require_preflight=False)
        with pytest.raises(RuntimeError, match="already active"):
            validate_cluster_create(canfar=canfar, store=store, req=req)

    def test_rejects_no_preflight(self, store: StateStore) -> None:
        canfar = MagicMock()
        _auth_ok(canfar)
        req = ClusterCreateRequest(name="x", require_preflight=True)
        with pytest.raises(RuntimeError, match="preflight"):
            validate_cluster_create(canfar=canfar, store=store, req=req)

    def test_min_joined_clamped_to_worker_count(self, store: StateStore) -> None:
        """min_joined > worker_count is silently clamped, not an error."""
        canfar = MagicMock()
        _auth_ok(canfar)
        req = ClusterCreateRequest(
            name="x", require_preflight=False, worker_count=2, min_joined=5
        )
        # validate_cluster_create clamps min_joined to max(1, min(min_joined, worker_count))
        # so this should not raise
        validate_cluster_create(canfar=canfar, store=store, req=req)

    def test_rejects_invalid_partial_policy(self, store: StateStore) -> None:
        canfar = MagicMock()
        _auth_ok(canfar)
        req = ClusterCreateRequest(
            name="x", require_preflight=False, partial_policy="unknown"
        )
        with pytest.raises(RuntimeError, match="partial_policy"):
            validate_cluster_create(canfar=canfar, store=store, req=req)

    def test_accepts_idle_without_preflight_when_optional(
        self, store: StateStore
    ) -> None:
        canfar = MagicMock()
        _auth_ok(canfar)
        req = ClusterCreateRequest(name="x", require_preflight=False)
        # Should not raise
        validate_cluster_create(canfar=canfar, store=store, req=req)

    def test_accepts_stopped_with_preflight(
        self, store: StateStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        canfar = MagicMock()
        _auth_ok(canfar)
        store.save(
            _state(
                phase="Stopped",
                preflight={"passed": True, "manager_ip": "10.0.0.5"},
            )
        )
        monkeypatch.setattr("astroai_workload.cluster.manager_pod_ip", lambda: "10.0.0.5")
        req = ClusterCreateRequest(name="x", require_preflight=True)
        validate_cluster_create(canfar=canfar, store=store, req=req)


class TestStopCluster:
    def test_full_stop_flow(
        self, store: StateStore, settings: ManagerSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        canfar = MagicMock()
        canfar.destroy.return_value = True
        canfar.session_info.return_value = {}  # no CANFAR info
        canfar.session_logs.return_value = ""  # no logs to persist
        store.save(
            _state(
                phase="Running",
                worker_count=2,
                workers=[
                    WorkerRecord(
                        session_id="w1", name="w1", phase="Ray Healthy", canfar_status="Running"
                    ),
                    WorkerRecord(
                        session_id="w2", name="w2", phase="Ray Healthy", canfar_status="Running"
                    ),
                ],
            )
        )
        with (
            patch("astroai_workload.cluster.archive_session_logs"),
            patch("astroai_workload.cluster.reconcile_cluster") as mock_reconcile,
        ):

            def reconcile_side(canfar=None, store=None, state=None, nodes=None):
                s = store.load() if store else state
                if s:
                    for w in s.workers:
                        w.phase = "Stopped"
                    s.phase = "Stopping"
                    store.save(s)
                return s

            mock_reconcile.side_effect = reconcile_side

            result = stop_cluster(canfar=canfar, store=store)

        assert result is not None
        assert result.phase == "Stopped"
        assert all(w.phase == "Stopped" for w in result.workers)

    def test_stop_empty_state(self, store: StateStore) -> None:
        canfar = MagicMock()
        result = stop_cluster(canfar=canfar, store=store)
        assert result is None


class TestRetryWorker:
    def test_retry_creates_replacement(
        self, store: StateStore, settings: ManagerSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        canfar = MagicMock()
        _auth_ok(canfar)
        canfar.session_logs.return_value = ""  # no logs to persist
        store.save(
            _state(
                phase="Degraded",
                worker_count=2,
                workers=[
                    WorkerRecord(
                        session_id="bad-w1",
                        name="ray-w-1",
                        phase="CANFAR Failed",
                        cores=2,
                        ram_gb=8,
                        gpus=0,
                    ),
                ],
            )
        )
        canfar.destroy.return_value = True
        canfar.create_headless.return_value = [
            SessionLaunch(session_id="new-sid", name="ray-retry-new"),
        ]
        canfar.wait_for_status.return_value = "Running"

        monkeypatch.setattr("astroai_workload.cluster.count_live_nodes", lambda *a, **k: 2)
        monkeypatch.setattr("astroai_workload.cluster.wait_for_node_count", lambda *a, **k: 3)

        with (
            patch("astroai_workload.cluster.archive_session_logs"),
            patch("astroai_workload.cluster.reconcile_cluster") as mock_reconcile,
        ):

            def reconcile_side(canfar=None, store=None, state=None, nodes=None):
                s = store.load() if store else state
                if s:
                    for w in s.workers:
                        if w.session_id == "new-sid":
                            w.ray_joined = True
                            w.phase = "Ray Healthy"
                return s

            mock_reconcile.side_effect = reconcile_side

            result = retry_worker(
                settings=settings,
                canfar=canfar,
                store=store,
                heartbeat_path="/hb",
                session_id="bad-w1",
            )

        assert result.phase == "Ray Healthy"
        assert result.ray_joined is True

    def test_retry_raises_on_unknown_session(
        self, store: StateStore, settings: ManagerSettings
    ) -> None:
        canfar = MagicMock()
        store.save(_state(phase="Running"))
        with pytest.raises(RuntimeError, match="Unknown worker"):
            retry_worker(
                settings=settings,
                canfar=canfar,
                store=store,
                heartbeat_path="/hb",
                session_id="nonexistent",
            )

    def test_retry_raises_when_not_retryable(
        self, store: StateStore, settings: ManagerSettings
    ) -> None:
        canfar = MagicMock()
        store.save(
            _state(
                phase="Running",
                workers=[
                    WorkerRecord(
                        session_id="w1", name="w1", phase="Ray Healthy", ray_joined=True
                    ),
                ],
            )
        )
        with pytest.raises(RuntimeError, match="not in retriable state"):
            retry_worker(
                settings=settings,
                canfar=canfar,
                store=store,
                heartbeat_path="/hb",
                session_id="w1",
            )


class TestGcTerminalClusterWorkers:
    def test_destroys_ghosts_for_stopped(
        self, store: StateStore, settings: ManagerSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        canfar = MagicMock()
        canfar.destroy.return_value = True
        canfar.session_logs.return_value = ""  # no logs to persist
        canfar.list_headless_sessions.return_value = []
        store.save(
            _state(
                phase="Stopped",
                workers=[
                    WorkerRecord(
                        session_id="ghost",
                        name="ray-w-ghost",
                        phase="Stopped",
                        canfar_status="Running",
                    ),
                ],
            )
        )
        monkeypatch.setattr("astroai_workload.cluster.clean_orphaned_workers", lambda *a, **k: [])

        with (
            patch("astroai_workload.cluster.archive_session_logs"),
            patch("astroai_workload.cluster.reconcile_cluster", return_value=None),
        ):
            result = gc_terminal_cluster_workers(
                settings=settings, canfar=canfar, store=store
            )

        assert result is not None
        canfar.destroy.assert_called()

    def test_does_not_destroy_for_running(
        self, store: StateStore, settings: ManagerSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        canfar = MagicMock()
        canfar.list_headless_sessions.return_value = []
        store.save(
            _state(
                phase="Running",
                workers=[
                    WorkerRecord(
                        session_id="active", name="w-active", phase="Ray Healthy"
                    ),
                ],
            )
        )
        monkeypatch.setattr("astroai_workload.cluster.clean_orphaned_workers", lambda *a, **k: [])

        with patch("astroai_workload.cluster.reconcile_cluster", return_value=None):
            result = gc_terminal_cluster_workers(
                settings=settings, canfar=canfar, store=store
            )
        # For Running phase, workers should NOT be destroyed
        canfar.destroy.assert_not_called()
        assert result is not None
        assert result.phase == "Running"

    def test_handles_no_saved_state(
        self, store: StateStore, settings: ManagerSettings
    ) -> None:
        canfar = MagicMock()
        canfar.list_headless_sessions.return_value = []
        canfar.destroy.return_value = True
        with patch("astroai_workload.cluster.reconcile_cluster", return_value=None):
            result = gc_terminal_cluster_workers(
                settings=settings, canfar=canfar, store=store
            )
        assert result is None


class TestCreateClusterEdgeCases:
    def test_min_joined_defaults_to_worker_count(
        self, store: StateStore, settings: ManagerSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When min_joined is None, it defaults to worker_count."""
        canfar = MagicMock()
        _auth_ok(canfar)
        monkeypatch.setattr("astroai_workload.cluster.manager_pod_ip", lambda: "10.0.0.1")
        store.save(
            _state(
                phase="Failed",
                preflight={"passed": True, "manager_ip": "10.0.0.1"},
            )
        )

        req = ClusterCreateRequest(
            name="test", worker_count=3, min_joined=None, partial_policy="accept_partial"
        )

        with patch("astroai_workload.cluster.prepare_cluster_create"):
            with patch("astroai_workload.cluster.count_live_nodes", return_value=1):
                canfar.create_headless.return_value = [
                    SessionLaunch(session_id=f"sid-{i}", name=f"ray-w-{i}")
                    for i in range(3)
                ]
                canfar.wait_for_status.return_value = "Running"
                monkeypatch.setattr("astroai_workload.cluster.count_live_nodes", lambda: 1)

                with (
                    patch("astroai_workload.cluster.reconcile_cluster") as mock_rec,
                    patch("astroai_workload.cluster.wait_for_node_count", return_value=4),
                ):

                    def rec_side(**kw):
                        s = kw.get("state") or store.load()
                        if s:
                            for w in s.workers:
                                w.ray_joined = True
                                w.phase = "Ray Healthy"
                            s.phase = "Running"
                            store.save(s)
                        return s

                    mock_rec.side_effect = rec_side

                    with patch("astroai_workload.cluster._archive_worker_logs"):
                        result = create_cluster_body_test(
                            settings=settings, canfar=canfar, store=store, req=req
                        )

            assert result.success is True
            state = store.load()
            assert state is not None
            assert state.min_joined == 3  # defaults to worker_count


def create_cluster_body_test(settings, canfar, store, req):
    """Helper: call the internal create body (after validation)."""
    from astroai_workload.cluster import _create_cluster_body

    return _create_cluster_body(
        settings=settings,
        canfar=canfar,
        store=store,
        heartbeat_path="/tmp/hb",
        req=req,
    )
