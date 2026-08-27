"""Tests for canfar_marimo project env activation (no marimo UI required)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Import the container helper without needing marimo installed.
_LIB = Path(__file__).resolve().parents[0]
sys.path.insert(0, str(_LIB))

from canfar_marimo import list_projects, project_env_python, use_project, work_dir  # noqa: E402


def test_list_projects_finds_cloned_style_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORK", str(tmp_path))
    (tmp_path / "notebooks").mkdir()
    proj = tmp_path / "mylab"
    proj.mkdir()
    (proj / "pixi.toml").write_text("[workspace]\n", encoding="utf-8")
    (tmp_path / "scratch-only").mkdir()
    found = list_projects(tmp_path)
    assert found == [proj]
    assert work_dir() == tmp_path


def test_use_project_activates_venv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import venv

    monkeypatch.setenv("WORK", str(tmp_path))
    proj = tmp_path / "uvproj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='uvproj'\nversion='0'\n", encoding="utf-8")
    (proj / "src").mkdir()
    venv.create(proj / ".venv", with_pip=False)
    py = project_env_python(proj)
    assert py is not None and py.is_file()

    msg = use_project(proj)
    assert "uvproj" in msg
    assert str(proj / "src") in sys.path
    assert Path(os.environ["VIRTUAL_ENV"]) == (proj / ".venv").resolve()
    assert os.environ["ASTROAI_MARIMO_PROJECT"] == str(proj.resolve())


def test_use_project_missing_env_raises(tmp_path: Path) -> None:
    proj = tmp_path / "bare"
    proj.mkdir()
    (proj / "pixi.toml").write_text("[workspace]\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match=r"\.pixi|\.venv"):
        use_project(proj)


def test_install_package_requires_active_project() -> None:
    from canfar_marimo import install_package

    os.environ.pop("ASTROAI_MARIMO_PROJECT", None)
    with pytest.raises(RuntimeError, match="No project activated"):
        install_package("rich")


def test_install_package_runs_pixi_add(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import venv

    from canfar_marimo import install_package

    proj = tmp_path / "pixiproj"
    proj.mkdir()
    (proj / "pixi.toml").write_text("[workspace]\nname = \"pixiproj\"\n", encoding="utf-8")
    # Fake env so project_env_python finds a python; install will call pixi.
    venv.create(proj / ".venv", with_pip=False)
    # Pretend it's a pixi env layout for kind detection via pixi.toml.
    calls: list[list[str]] = []

    def fake_run(cmd, check=True, capture_output=True, text=True):  # noqa: ANN001
        calls.append(list(cmd))

        class R:
            stdout = "ok"
            stderr = ""
            returncode = 0

        return R()

    monkeypatch.setattr("canfar_marimo.subprocess.run", fake_run)
    monkeypatch.setattr(
        "canfar_marimo.project_env_python",
        lambda p: p / ".venv" / "bin" / "python",
    )
    monkeypatch.setattr("canfar_marimo.use_project", lambda p: f"activated {p}")
    msg = install_package("rich", project=proj)
    assert "rich" in msg
    assert calls and calls[0][:2] == ["pixi", "add"]
    assert "--manifest-path" in calls[0]
    assert str(proj.resolve()) in calls[0]
    assert "--pypi" in calls[0]
    assert "rich" in calls[0]


def test_use_project_clears_previous_site_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import venv

    monkeypatch.setenv("WORK", str(tmp_path))
    projects = []
    for name in ("a", "b"):
        proj = tmp_path / name
        proj.mkdir()
        (proj / "pyproject.toml").write_text(
            f"[project]\nname='{name}'\nversion='0'\n", encoding="utf-8"
        )
        (proj / "src").mkdir()
        venv.create(proj / ".venv", with_pip=False)
        projects.append(proj)

    use_project(projects[0])
    assert str(projects[0] / "src") in sys.path
    use_project(projects[1])
    assert str(projects[1] / "src") in sys.path
    assert str(projects[0] / "src") not in sys.path


def test_use_project_accepts_venv_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import venv

    monkeypatch.setenv("WORK", str(tmp_path))
    proj = tmp_path / "uvproj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='uvproj'\nversion='0'\n", encoding="utf-8")
    venv.create(proj / ".venv", with_pip=False)
    msg = use_project(proj / ".venv")
    assert "uvproj" in msg
    assert os.environ["ASTROAI_MARIMO_PROJECT"] == str(proj.resolve())


