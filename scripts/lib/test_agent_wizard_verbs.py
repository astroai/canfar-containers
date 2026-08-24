"""Assert hub maps to astroai-lab verbs + honest compute ensure."""

from __future__ import annotations

import importlib.util
import json
import threading
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("agent_wizard", ROOT / "agent-wizard.py")
assert SPEC and SPEC.loader
wiz = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wiz)

REMOVED = {"report", "addons", "catalog", "interact", "repair", "clean", "add"}


def _assert_lean(calls: list[list[str]]) -> None:
    for c in calls:
        if "agent" not in c:
            continue
        i = c.index("agent")
        verb = c[i + 1] if i + 1 < len(c) else ""
        assert verb not in REMOVED, c


def test_addons_and_catalog_use_list_config() -> None:
    calls: list[list[str]] = []

    def fake(args: list[str], *, timeout: int | None = None) -> tuple[int, str, str]:
        calls.append(args)
        if "plugins" in args and args[-1] == "list":
            return (
                0,
                '[{"id":"ponytail","kind":"skill","tags":["lean"],'
                '"any_installed":false,"summary":"x"}]',
                "",
            )
        if args[-1] == "list":
            return (
                0,
                '{"ok":true,"agents":[{"id":"kilo","agent":"kilo",'
                '"binary":true,"summary":"cli"}]}',
                "",
            )
        return 0, "{}", ""

    with patch.object(wiz, "_run_lab", side_effect=fake):
        rc, rows, _ = wiz._plugins_from_list_config("lean")
        assert rc == 0
        assert rows and rows[0]["installed"] is False
        rc2, items, _ = wiz._catalog_items()
        assert rc2 == 0
        kinds = {i["kind"] for i in items}
        assert "agent" in kinds and "skill" in kinds
    _assert_lean(calls)


def test_install_by_tag_loops_plugins_install() -> None:
    calls: list[list[str]] = []

    def fake(args: list[str], *, timeout: int | None = None) -> tuple[int, str, str]:
        calls.append(args)
        if "plugins" in args and args[-1] == "list":
            return (
                0,
                '[{"id":"ponytail","kind":"skill","tags":["lean"],"any_installed":false},'
                '{"id":"other","kind":"skill","tags":["science"],"any_installed":false}]',
                "",
            )
        if "plugins" in args and "install" in args:
            pid = args[-1]
            return (
                0,
                f'{{"ok":true,"plugin":"{pid}",'
                f'"actions":[{{"id":"{pid}","status":"ok"}}]}}',
                "",
            )
        return 1, "", "unexpected"

    with patch.object(wiz, "_run_lab", side_effect=fake):
        rc, data = wiz._install_plugins_by_tag("lean")
    assert rc == 0
    assert data["ok"]
    assert any(c[-2:] == ["install", "ponytail"] for c in calls)
    assert not any("other" == c[-1] for c in calls if "install" in c)
    _assert_lean(calls)


def test_compute_ensure_idempotent_and_wires() -> None:
    wire = MagicMock()
    wire.find_manager_sessions.return_value = [
        {"status": "Running", "image": "astroai/ray-manager", "connectURL": "https://mgr/"}
    ]
    wire._session_status.side_effect = lambda m: m["status"]
    wire._session_connect_url.side_effect = lambda m: m.get("connectURL", "")
    wire.jobs_url_from_connect.return_value = "https://mgr/dashboard"
    wire.wire_orx.return_value = {"address": "https://mgr/dashboard"}

    ensure_cmds: list[list[str]] = []

    def fake_cmd(cmd: list[str], *, timeout: int) -> tuple[int, str, str]:
        if cmd[:2] == ["astroai", "cluster"]:
            ensure_cmds.append(cmd)
            return (
                0,
                json.dumps(
                    {
                        "jobs_address": "https://mgr/dashboard",
                        "joined_workers": 0,
                        "cluster_phase": "running",
                        "manager_url": "https://mgr/",
                    }
                ),
                "",
            )
        return 0, "", ""

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        with (
            patch.object(wiz, "_load_wire", return_value=wire),
            patch.object(wiz, "WIRE_OPENRESEARCH", True),
            patch.object(wiz, "shutil") as sh,
            patch.object(wiz, "_run_cmd", side_effect=fake_cmd),
            patch.object(wiz.Path, "home", return_value=home),
        ):
            sh.which.return_value = "/usr/bin/astroai"
            data = wiz._compute_ensure()

        env = (home / ".config" / "canfar" / "lab" / "ray-manager.env").read_text()

    assert data["ok"] is True
    assert data["jobs_address"] == "https://mgr/dashboard"
    assert "autoscaling-env" in data["steps"]
    assert "wire-orx" in data["steps"]
    wire.wire_orx.assert_called_once()
    assert "create" not in data["steps"]
    assert "manager-exists" in data["steps"]
    assert "astroai" in ensure_cmds[0] and "cluster" in ensure_cmds[0]
    assert "start" in ensure_cmds[0] and "--json" in ensure_cmds[0]
    assert "--autoscaling" not in ensure_cmds[0]
    assert "RAY_AUTOSCALING_ENABLED=1" in env
    assert "do not add workers" in data["user_message"]


def test_compute_ensure_runs_in_background_and_status_polls() -> None:
    """POST starts a thread and returns fast; GET /status reports progress."""
    import time as _time

    gate = threading.Event()

    def slow_ensure() -> dict:
        gate.wait(timeout=5)
        return {"ok": True, "summary": "done", "user_message": "ready", "steps": ["x"]}

    with (
        patch.object(wiz, "_compute_ensure", staticmethod(slow_ensure)),
    ):
        wiz._ENSURE_STATE.update(running=False, steps=[], result=None)
        started = _time.time()
        payload = wiz._start_compute_ensure()
        assert payload["ok"] is True and payload["running"] is True
        assert _time.time() - started < 1.0  # returned without running the job

        status = wiz._compute_status()
        assert status["running"] is True

        # Second start while running must not spawn another job.
        again = wiz._start_compute_ensure()
        assert again.get("running") is True and "started" not in again

        gate.set()
        for _ in range(100):
            if not wiz._compute_status()["running"]:
                break
            _time.sleep(0.05)
        final = wiz._compute_status()
        assert final["running"] is False
        assert final["ok"] is True
        assert final["summary"] == "done"


def test_back_link_prefers_saved_referrer_over_marker() -> None:
    """Root-mounted ingress (marker at index 0) must not fall back to '/'."""
    html = wiz.INDEX_HTML
    assert "sessionStorage.getItem('astroai-hub-back')" in html
    assert "document.referrer" in html
    # The old heuristic resolved the bare domain when i == 0; now it requires i > 0.
    assert "if (i > 0)" in html


def test_index_html_agent_table() -> None:
    html = wiz.INDEX_HTML
    assert "Start batch compute" in html
    assert "Setup agents" not in html
    assert "btn-setup" not in html
    assert "<th>Agent</th>" in html
    assert "<th>Bin</th>" in html
    assert "<th>Cfg</th>" in html
    assert "<th>Where</th>" in html
    assert "<th>Ver</th>" in html
    assert "api/setup?agent=" in html
    assert "api/install?tool=" in html
    assert "Install Kilo" not in html
    assert "kilo" not in html.lower()
    assert "Advanced" not in html
    assert "cheat sheet" not in html.lower()
    assert "Install lean addons" not in html
    assert "api/catalog" not in html
    assert "/astroai-' + 'agents" in html
    assert "id=\"back-link\"" in html


def test_agent_report_returns_full_list() -> None:
    payload = {
        "ok": True,
        "agents": [
            {
                "id": "kilo",
                "agent": "kilo",
                "binary_ok": True,
                "config_ok": False,
                "binary_source": "managed",
                "version": "1.2.3",
            }
        ],
        "setup": {"stamp": "2026-08-14"},
        "issues": [],
    }

    def fake(args: list[str], *, timeout: int | None = None) -> tuple[int, str, str]:
        assert args[-1] == "list"
        return 0, json.dumps(payload), ""

    with patch.object(wiz, "_run_lab", side_effect=fake), patch.object(
        wiz, "_log_tail", return_value=""
    ):
        code, data = wiz._agent_report()
    assert code == 200
    assert data["agents"][0]["id"] == "kilo"
    assert data["cli_exit"] == 0


def test_setup_is_scoped_to_agent_id() -> None:
    calls: list[list[str]] = []

    def fake(args: list[str], *, timeout: int | None = None) -> tuple[int, str, str]:
        calls.append(args)
        return 0, '{"ok":true,"actions":["created config"],"errors":[]}', ""

    with patch.object(wiz, "_run_lab", side_effect=fake):
        data = wiz._setup_payload("kilo")
    assert data["ok"] is True
    assert data["summary"] == "setup kilo ok"
    assert any(c[-2:] == ["setup", "kilo"] for c in calls)
    assert not any(c[-1] == "setup" for c in calls)
    _assert_lean(calls)


def test_safe_agent_id_rejects_junk() -> None:
    assert wiz._safe_agent_id("kilo") == "kilo"
    assert wiz._safe_agent_id("open-claw") == "open-claw"
    assert wiz._safe_agent_id("kilo;rm") is None
    assert wiz._safe_agent_id("../etc") is None
    assert wiz._safe_agent_id("") is None
    assert wiz._safe_agent_id(None) is None


if __name__ == "__main__":
    test_addons_and_catalog_use_list_config()
    test_install_by_tag_loops_plugins_install()
    test_compute_ensure_idempotent_and_wires()
    test_compute_ensure_runs_in_background_and_status_polls()
    test_back_link_prefers_saved_referrer_over_marker()
    test_index_html_agent_table()
    test_agent_report_returns_full_list()
    test_setup_is_scoped_to_agent_id()
    test_safe_agent_id_rejects_junk()
    print("ok")
