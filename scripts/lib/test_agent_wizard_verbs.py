"""Assert hub maps to astroai-lab verbs + honest compute ensure."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import threading
import time
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
                '[{"id":"ponytail-rule","kind":"rule","tags":["lean"],'
                '"any_installed":false,"summary":"x"}]',
                "",
            )
        if args[-1] == "list":
            return (
                0,
                '{"ok":true,"agents":[{"id":"kilo","agent":"kilo","binary":true,"summary":"cli"}]}',
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
        assert "agent" in kinds and "rule" in kinds
    _assert_lean(calls)


def test_install_by_tag_loops_plugins_install() -> None:
    calls: list[list[str]] = []

    def fake(args: list[str], *, timeout: int | None = None) -> tuple[int, str, str]:
        calls.append(args)
        if "plugins" in args and args[-1] == "list":
            return (
                0,
                '[{"id":"ponytail-rule","kind":"rule","tags":["lean"],"any_installed":false},'
                '{"id":"other","kind":"mcp","tags":["science"],"any_installed":false}]',
                "",
            )
        if "plugins" in args and "install" in args:
            pid = args[-1]
            return (
                0,
                f'{{"ok":true,"plugin":"{pid}","actions":[{{"id":"{pid}","status":"ok"}}]}}',
                "",
            )
        return 1, "", "unexpected"

    with patch.object(wiz, "_run_lab", side_effect=fake):
        rc, data = wiz._install_plugins_by_tag("lean")
    assert rc == 0
    assert data["ok"]
    assert any(c[-2:] == ["install", "ponytail-rule"] for c in calls)
    assert not any(c[-1] == "other" for c in calls if "install" in c)
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
        if cmd[:2] == ["/usr/bin/canfar-lab", "cluster"]:
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
            patch.object(wiz, "WIRE_ORX", True),
            patch.object(wiz, "WIRE_OPENRESEARCH", True),
            patch.object(wiz, "shutil") as sh,
            patch.object(wiz, "_run_cmd", side_effect=fake_cmd),
            patch.object(wiz, "_lab_bin", return_value="/usr/bin/canfar-lab"),
            patch.object(wiz.Path, "home", return_value=home),
        ):
            sh.which.return_value = "/usr/bin/canfar-lab"
            data = wiz._compute_ensure()

        env = (home / ".config" / "canfar" / "lab" / "ray-manager.env").read_text()

    assert data["ok"] is True
    assert data["jobs_address"] == "https://mgr/dashboard"
    assert "autoscaling-env" in data["steps"]
    assert "wire-orx" in data["steps"]
    wire.wire_orx.assert_called_once()
    assert "create" not in data["steps"]
    assert "manager-exists" in data["steps"]
    assert "/usr/bin/canfar-lab" in ensure_cmds[0] and "cluster" in ensure_cmds[0]
    assert "start" in ensure_cmds[0] and "--json" in ensure_cmds[0]
    assert "--autoscaling" not in ensure_cmds[0]
    assert "RAY_AUTOSCALING_ENABLED=1" in env
    assert "do not add workers" in data["user_message"]


def test_compute_ensure_studio_skips_orx_wire() -> None:
    """Studio needs ray-manager/Jobs URL only — never OpenResearch wire_orx."""
    wire = MagicMock()
    wire.find_manager_sessions.return_value = [
        {"status": "Running", "image": "astroai/ray-manager", "connectURL": "https://mgr/"}
    ]
    wire._session_status.side_effect = lambda m: m["status"]
    wire._session_connect_url.side_effect = lambda m: m.get("connectURL", "")
    wire.jobs_url_from_connect.return_value = "https://mgr/dashboard"

    def fake_cmd(cmd: list[str], *, timeout: int) -> tuple[int, str, str]:
        if cmd[:2] == ["/usr/bin/canfar-lab", "cluster"]:
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
            patch.object(wiz, "WIRE_ORX", False),
            patch.object(wiz, "WIRE_OPENRESEARCH", False),
            patch.object(wiz, "shutil") as sh,
            patch.object(wiz, "_run_cmd", side_effect=fake_cmd),
            patch.object(wiz, "_lab_bin", return_value="/usr/bin/canfar-lab"),
            patch.object(wiz.Path, "home", return_value=home),
        ):
            sh.which.return_value = "/usr/bin/canfar-lab"
            data = wiz._compute_ensure()

    assert data["ok"] is True
    assert data["jobs_address"] == "https://mgr/dashboard"
    assert "wire-orx" not in data["steps"]
    wire.wire_orx.assert_not_called()
    assert "OpenResearch is wired" not in data["user_message"]


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


def test_index_html_hub_sections() -> None:
    html = wiz.INDEX_HTML
    assert "Start batch compute" in html
    assert "Model access" in html and "Coding agents" in html
    assert "api/keys" in html and "api/jobs" in html
    assert "'X-AstroAI-Hub': '1'" in html
    assert "api/install?tool=" not in html and "api/setup?agent=" not in html
    assert "fonts.googleapis.com" not in html
    assert 'type="password"' in html and 'autocomplete="off"' in html
    assert "More agents" not in html  # every agent is listed in one grid
    assert "/astroai-' + 'agents" in html
    assert 'id="back-link"' in html
    assert "npx skills add astroai/canfar-skills" in html
    assert "__" not in html.split("<script>")[0].replace("__proto__", "")


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

    with (
        patch.object(wiz, "_run_lab", side_effect=fake),
        patch.object(wiz, "_log_tail", return_value=""),
    ):
        code, data = wiz._agent_report()
    assert code == 200
    assert data["agents"][0]["id"] == "kilo"
    assert data["cli_exit"] == 0


def _wait_job() -> dict:
    import time as _time

    for _ in range(200):
        job = wiz._job_snapshot()
        if not job["running"]:
            return job
        _time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_job_runs_lab_verb_streams_log_and_serializes() -> None:
    script = Path(tempfile.mkdtemp()) / "fake-lab"
    script.write_text(
        "#!/bin/sh\nprintf '\\033[32mstep one\\033[0m\\n'\necho \"args: $*\"\nsleep 0.3\n"
    )
    script.chmod(0o755)
    with patch.object(wiz, "_lab_bin", return_value=str(script)):
        code, job = wiz._start_job("setup", "kilo")
        assert code == 202 and job["running"] is True
        busy, payload = wiz._start_job("install", "codex")
        assert busy == 409 and "still running" in payload["error"]
        done = _wait_job()
    assert done["ok"] is True and done["exit"] == 0
    assert done["log"] == ["step one", "args: --yes agent setup kilo"]
    assert done["summary"] == "kilo set up"


def test_job_failure_reports_exit_and_rejects_unknown_action() -> None:
    script = Path(tempfile.mkdtemp()) / "fake-lab"
    script.write_text("#!/bin/sh\necho boom >&2\nexit 3\n")
    script.chmod(0o755)
    with patch.object(wiz, "_lab_bin", return_value=str(script)):
        assert wiz._start_job("rm-rf", "kilo")[0] == 400
        wiz._start_job("install", "kilo")
        done = _wait_job()
    assert done["ok"] is False and done["exit"] == 3
    assert done["log"] == ["boom"]
    assert "failed (exit 3)" in done["summary"]


def test_startup_restores_remembered_agents_as_a_hub_job() -> None:
    script = Path(tempfile.mkdtemp()) / "fake-lab"
    script.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *--dry-run*) echo \'{"ok": true, "tools": ["codex", "opencode"], "results": []}\' ;;\n'
        '  *) echo "args: $*" ;;\n'
        "esac\n"
    )
    script.chmod(0o755)
    with patch.object(wiz, "_lab_bin", return_value=str(script)):
        wiz._restore_agents_on_start()
        done = _wait_job()
    assert done["action"] == "restore"
    assert done["log"] == ["args: --yes agent install --restore"]
    assert done["summary"] == "codex, opencode restored"


def test_startup_restore_single_agent_and_nothing_missing() -> None:
    started: list[tuple[str, str]] = []
    with patch.object(wiz, "_start_job", side_effect=lambda a, b: started.append((a, b))):
        with patch.object(wiz, "_run_lab", return_value=(0, '{"ok": true, "tool": "kilo"}', "")):
            wiz._restore_agents_on_start()
        payload = '{"ok": true, "tools": [], "results": [], "errors": []}'
        with patch.object(wiz, "_run_lab", return_value=(0, payload, "")):
            wiz._restore_agents_on_start()
    assert started == [("restore", "kilo")]


def test_keys_set_passes_value_on_stdin_only() -> None:
    seen: dict = {}

    def fake(args, *, timeout=None, input_text=None):
        seen["args"], seen["input"] = args, input_text
        return 0, '{"key":"OPENROUTER_API_KEY","present":true}', ""

    with patch.object(wiz, "_run_lab", side_effect=fake):
        code, data = wiz._keys_change("OPENROUTER_API_KEY", "  sk-or-secret-123  ")
    assert code == 200 and data == {"ok": True, "key": "OPENROUTER_API_KEY", "present": True}
    assert seen["args"] == ["--json", "agent", "keys", "set", "OPENROUTER_API_KEY"]
    assert "sk-or-secret-123" not in " ".join(seen["args"])
    assert seen["input"] == "sk-or-secret-123\n"
    assert "sk-or-secret" not in json.dumps(data)


def test_saved_key_asks_for_an_openscience_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ASTROAI_STUDIO_STATE", str(tmp_path))
    flag = tmp_path / "openscience.restart"
    with patch.object(wiz, "_run_lab", return_value=(1, "", "Error: bad key\n")):
        wiz._keys_change("OPENAI_API_KEY", "short")
    assert not flag.exists()
    with patch.object(wiz, "_run_lab", return_value=(0, "{}", "")):
        wiz._keys_change("OPENAI_API_KEY", None)
    assert flag.exists()


def test_keys_change_validates_and_surfaces_cli_error() -> None:
    assert wiz._keys_change("bad name", "x")[0] == 400
    assert wiz._keys_change("OPENAI_API_KEY", "a\nb")[0] == 400

    def fake(args, *, timeout=None, input_text=None):
        return 1, "", "Error: That does not look like an API key.\n  hint: paste it again\n"

    with patch.object(wiz, "_run_lab", side_effect=fake):
        code, data = wiz._keys_change("OPENAI_API_KEY", "short")
    assert code == 400 and data["error"] == "Error: That does not look like an API key."

    cli_json = json.dumps({"ok": False, "error": "Not an API key.", "hint": "Paste it again."})
    with patch.object(wiz, "_run_lab", return_value=(1, cli_json, "")):
        code, data = wiz._keys_change("OPENAI_API_KEY", "short")
    assert code == 400 and data["error"] == "Not an API key. Paste it again."

    calls = []
    with patch.object(wiz, "_run_lab", side_effect=lambda a, **k: calls.append(a) or (0, "{}", "")):
        assert wiz._keys_change("OPENAI_API_KEY", None)[1]["present"] is False
    assert calls == [["--json", "agent", "keys", "unset", "OPENAI_API_KEY"]]


def test_canfar_auth_requires_unexpired_credential() -> None:
    import time as _time

    def show(payload):
        return patch.object(wiz, "_run_cmd", return_value=(0, json.dumps(payload), ""))

    with (
        patch.object(wiz.shutil, "which", return_value="/usr/bin/canfar"),
        patch.object(wiz, "_cert_expiry", return_value=None),
    ):
        with show({"active": True, "expiry": None, "name": "CADC"}):
            ok, line = wiz._canfar_auth_line()
            assert ok is False and "canfar login" in line
        with show({"active": True, "expiry": _time.time() - 60, "name": "CADC"}):
            ok, line = wiz._canfar_auth_line()
            assert ok is False and "expired" in line
        with show({"active": True, "expiry": _time.time() + 5 * 86400, "name": "CADC"}):
            ok, line = wiz._canfar_auth_line()
            assert ok is True and line.startswith("CADC") and "days left" in line
        with show({"active": True, "expiry": "2999-01-01T00:00:00Z", "name": "CADC"}):
            assert wiz._canfar_auth_line()[0] is True
        with patch.object(wiz, "_run_cmd", return_value=(1, "", "boom")):
            assert wiz._canfar_auth_line()[0] is False


def _make_proxy_pem(path: Path, days: int) -> None:
    import subprocess as sp

    key, cert = path.with_suffix(".key"), path.with_suffix(".crt")
    sp.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=probe",
            "-days",
            str(days),
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )
    path.write_text(cert.read_text() + key.read_text())


def test_canfar_session_proxy_cert_counts_as_login(tmp_path: Path, monkeypatch) -> None:
    """Inside a Skaha session `canfar auth show` gives expiry null for the
    session-issued ~/.ssl/cadcproxy.pem; the certificate itself is the login."""
    ssl_dir = tmp_path / ".ssl"
    ssl_dir.mkdir()
    pem = ssl_dir / "cadcproxy.pem"
    _make_proxy_pem(pem, days=7)
    exp = wiz._cert_expiry(pem)
    assert exp is not None and 6 * 86400 < exp - time.time() <= 7 * 86400

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("skaha_sessionid", "ao1z1qsf")
    show = {
        "active": True,
        "expiry": None,
        "idp": "cadc",
        "mode": "x509",
        "name": "Canadian Astronomy Data Centre",
        "server": "canfar",
    }
    with (
        patch.object(wiz.shutil, "which", return_value="/usr/bin/canfar"),
        patch.object(wiz, "_run_cmd", return_value=(0, json.dumps(show), "")),
    ):
        ok, line = wiz._canfar_auth_line()
        assert ok is True, line
        assert "signed in by this CANFAR session" in line and "7 days left" in line
        monkeypatch.delenv("skaha_sessionid")
        ok, line = wiz._canfar_auth_line()
        assert ok is True and line.endswith("days left") and "session" not in line
        pem.unlink()
        assert wiz._canfar_auth_line()[0] is False
        with patch.object(wiz, "_cert_expiry", return_value=time.time() - 60):
            ok, line = wiz._canfar_auth_line()
        assert ok is False and "expired" in line


def test_http_post_requires_hub_header_and_json() -> None:
    import http.client

    server = wiz.ThreadingHTTPServer(("127.0.0.1", 0), wiz.WizardHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:

        def post(path, body, headers):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("POST", path, body=body, headers=headers)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read() or b"{}")

        status, _ = post("/api/keys", '{"key":"OPENAI_API_KEY","value":"x"}', {})
        assert status == 403
        status, _ = post("/api/keys", "not json", {"X-AstroAI-Hub": "1"})
        assert status == 400
        status, data = post(
            "/api/jobs", '{"action":"install","agent":"../x"}', {"X-AstroAI-Hub": "1"}
        )
        assert status == 400 and "agent" in data["error"]
    finally:
        server.shutdown()


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
    test_compute_ensure_studio_skips_orx_wire()
    test_compute_ensure_runs_in_background_and_status_polls()
    test_back_link_prefers_saved_referrer_over_marker()
    test_index_html_hub_sections()
    test_agent_report_returns_full_list()
    test_safe_agent_id_rejects_junk()
    print("ok")


def test_create_manager_records_steps() -> None:
    wire = MagicMock()
    wire.find_manager_sessions.return_value = []
    with patch.object(wiz, "_run_cmd", return_value=(0, "raymgr created", "")):
        assert wiz._create_manager_if_needed(wire) == (
            True,
            "ray-manager session created",
            ["create"],
        )
    with patch.object(wiz, "_run_cmd", return_value=(1, "", "session already exists")):
        ok, _, steps = wiz._create_manager_if_needed(wire)
        assert ok and steps == ["create-exists"]
