"""scripts/openscience-canfar.sh: environment, stdin, and the ~/.openscience lease."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[1] / "openscience-canfar.sh"

FAKE = """#!/bin/bash
echo "data=${OPENSCIENCE_DATA_DIR:-default} bootstrap=${OPENSCIENCE_SKIP_ENVIRONMENT_BOOTSTRAP}"
echo "autoupdate_off=${OPENSCIENCE_DISABLE_AUTOUPDATE} key=${ANTHROPIC_API_KEY:+set}"
echo "python=$(command -v python3)"
echo "args=$*"
if [[ "${1:-}" == "stdin" ]]; then cat; fi
if [[ "${1:-}" == "sleep" ]]; then
    trap 'echo got-term >"${FAKE_MARK}"; exit 0' TERM
    sleep 30 & wait
fi
"""


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    (home / ".astroai" / "lab").mkdir(parents=True)
    (home / ".astroai" / "lab" / ".env").write_text("ANTHROPIC_API_KEY=sk-test\n")
    fake = tmp_path / "openscience"
    fake.write_text(FAKE)
    fake.chmod(0o755)
    venv = tmp_path / "venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python3").write_text("#!/bin/sh\n")
    (venv / "python3").chmod(0o755)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "SCRATCH": str(scratch),
        "skaha_sessionid": "sess-a",
        "OPENSCIENCE_BIN": str(fake),
        "ASTROAI_SCIENCE_VENV": str(venv.parent),
        "ASTROAI_OPENSCIENCE_HEARTBEAT": "1",
        "FAKE_MARK": str(tmp_path / "mark"),
    }


def run(env: dict[str, str], *args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(WRAPPER), *args],
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def lease(env: dict[str, str]) -> Path:
    return Path(env["HOME"]) / ".openscience" / ".canfar-lease"


def test_env_keys_python_and_stdin(env: dict[str, str]) -> None:
    out = run(env, "stdin", "--x", stdin="hello from stdin\n")
    assert out.returncode == 0, out.stderr
    assert "data=default bootstrap=1" in out.stdout
    assert "autoupdate_off=1 key=set" in out.stdout
    assert f"python={env['ASTROAI_SCIENCE_VENV']}/bin/python3" in out.stdout
    assert "args=stdin --x" in out.stdout
    assert "hello from stdin" in out.stdout
    assert not lease(env).exists()


def test_other_live_session_falls_back_to_scratch(env: dict[str, str]) -> None:
    lease(env).parent.mkdir(parents=True)
    lease(env).write_text("sess-b 123\n")
    out = run(env, "run")
    assert f"data={env['SCRATCH']}/.openscience" in out.stdout
    assert "in use by session sess-b" in out.stderr
    assert lease(env).read_text() == "sess-b 123\n"


def test_stale_lease_is_taken_over(env: dict[str, str]) -> None:
    lease(env).parent.mkdir(parents=True)
    lease(env).write_text("sess-b 123\n")
    old = time.time() - 600
    os.utime(lease(env), (old, old))
    out = run(env, "run")
    assert "data=default" in out.stdout
    assert out.stderr == ""
    assert not lease(env).exists()


def test_short_run_keeps_long_serve_lease_and_term_reaches_child(env: dict[str, str]) -> None:
    serve = subprocess.Popen(
        ["bash", str(WRAPPER), "sleep"], env=env, stdout=subprocess.PIPE, text=True
    )
    for _ in range(50):
        if lease(env).exists():
            break
        time.sleep(0.1)
    held = lease(env).read_text()
    assert held == f"sess-a {serve.pid}\n"
    out = run(env, "run")
    assert "data=default" in out.stdout
    assert lease(env).read_text() == held
    serve.send_signal(signal.SIGTERM)
    assert serve.wait(timeout=10) == 143
    assert Path(env["FAKE_MARK"]).read_text().strip() == "got-term"
    assert not lease(env).exists()


def test_explicit_data_dir_skips_lease(env: dict[str, str], tmp_path: Path) -> None:
    env["OPENSCIENCE_DATA_DIR"] = str(tmp_path / "mine")
    out = run(env, "run")
    assert f"data={tmp_path}/mine" in out.stdout
    assert not lease(env).exists()
