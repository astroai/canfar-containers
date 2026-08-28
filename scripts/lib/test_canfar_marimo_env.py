"""Tests for canfar_marimo project env activation and auto-discovery."""

from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Import the container helper without needing marimo installed.
_LIB = Path(__file__).resolve().parents[0]
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import canfar_marimo
from canfar_marimo import (
    AstroAIProjectFinder,
    auto_project,
    enable_auto_environment,
    find_env_site_packages,
    find_project_root,
    install_package,
    list_projects,
    project_env_python,
    project_env_root,
    use_project,
    work_dir,
)


class CanfarMarimoEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.tmp_path = Path(self.temp_dir).resolve()
        self.orig_work = os.environ.get("WORK")
        self.orig_proj = os.environ.get("ASTROAI_MARIMO_PROJECT")
        self.orig_venv = os.environ.get("VIRTUAL_ENV")
        self.orig_path = os.environ.get("PATH", "")
        self.orig_sys_path = list(sys.path)
        os.environ["WORK"] = str(self.tmp_path)

    def tearDown(self) -> None:
        canfar_marimo._clear_previous_activation()
        sys.path[:] = self.orig_sys_path
        if self.orig_work is not None:
            os.environ["WORK"] = self.orig_work
        else:
            os.environ.pop("WORK", None)
        if self.orig_proj is not None:
            os.environ["ASTROAI_MARIMO_PROJECT"] = self.orig_proj
        else:
            os.environ.pop("ASTROAI_MARIMO_PROJECT", None)
        if self.orig_venv is not None:
            os.environ["VIRTUAL_ENV"] = self.orig_venv
        else:
            os.environ.pop("VIRTUAL_ENV", None)
        os.environ["PATH"] = self.orig_path
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_list_projects_finds_cloned_style_dirs(self) -> None:
        (self.tmp_path / "notebooks").mkdir()
        proj = self.tmp_path / "mylab"
        proj.mkdir()
        (proj / "pixi.toml").write_text("[workspace]\n", encoding="utf-8")
        (self.tmp_path / "scratch-only").mkdir()
        found = list_projects(self.tmp_path)
        self.assertEqual(found, [proj])
        self.assertEqual(work_dir(), self.tmp_path)

    def test_find_project_root_and_site_packages(self) -> None:
        proj = self.tmp_path / "zensus"
        sub = proj / "notebooks" / "deep"
        sub.mkdir(parents=True)
        (proj / "pixi.toml").write_text("[workspace]\n", encoding="utf-8")
        sp = proj / ".pixi" / "envs" / "default" / "lib" / "python3.13" / "site-packages"
        sp.mkdir(parents=True)
        (proj / ".pixi" / "envs" / "default" / "bin").mkdir(parents=True)
        (proj / ".pixi" / "envs" / "default" / "bin" / "python").touch()

        # Check finding project root from sub-directory
        root = find_project_root(sub)
        self.assertEqual(root, proj)

        # Check finding site-packages
        env_root = project_env_root(proj)
        self.assertEqual(env_root, proj / ".pixi" / "envs" / "default")
        found_sp = find_env_site_packages(env_root)
        self.assertEqual(found_sp, [sp])

    def test_use_project_with_name_and_auto_detect(self) -> None:
        proj = self.tmp_path / "zensus"
        proj.mkdir()
        (proj / "pixi.toml").write_text("[workspace]\n", encoding="utf-8")
        (proj / "src").mkdir()
        sp = proj / ".pixi" / "envs" / "default" / "lib" / "python3.13" / "site-packages"
        sp.mkdir(parents=True)
        (proj / ".pixi" / "envs" / "default" / "bin").mkdir(parents=True)
        (proj / ".pixi" / "envs" / "default" / "bin" / "python").touch()

        # 1. Activate by relative project name ("zensus")
        msg = use_project("zensus")
        self.assertIn("zensus", msg)
        self.assertIn(str(sp), sys.path)
        self.assertIn(str(proj / "src"), sys.path)
        self.assertEqual(os.environ["ASTROAI_MARIMO_PROJECT"], str(proj.resolve()))

        # 2. Auto-detect when in project root
        canfar_marimo._clear_previous_activation()
        orig_cwd = os.getcwd()
        try:
            os.chdir(proj)
            auto_msg = auto_project()
            self.assertIn("zensus", auto_msg)
            self.assertIn(str(sp), sys.path)
        finally:
            os.chdir(orig_cwd)

    def test_use_project_clears_previous_site_packages(self) -> None:
        projects = []
        for name in ("a", "b"):
            proj = self.tmp_path / name
            proj.mkdir()
            (proj / "pyproject.toml").write_text(f"[project]\nname='{name}'\n", encoding="utf-8")
            (proj / "src").mkdir()
            sp = proj / ".venv" / "lib" / "python3.13" / "site-packages"
            sp.mkdir(parents=True)
            (proj / ".venv" / "bin").mkdir(parents=True)
            (proj / ".venv" / "bin" / "python").touch()
            (proj / ".venv" / "pyvenv.cfg").write_text("version = 3.13.0\n", encoding="utf-8")
            projects.append(proj)

        use_project(projects[0])
        self.assertIn(str(projects[0] / "src"), sys.path)
        use_project(projects[1])
        self.assertIn(str(projects[1] / "src"), sys.path)
        self.assertNotIn(str(projects[0] / "src"), sys.path)

    def test_astroai_project_finder_auto_resolves_import(self) -> None:
        """Simulate user's exact workflow: clone repo with pixi env, then import from notebook."""
        enable_auto_environment()

        proj = self.tmp_path / "zensus"
        proj.mkdir()
        (proj / "pixi.toml").write_text("[workspace]\n", encoding="utf-8")
        sp = proj / ".pixi" / "envs" / "default" / "lib" / "python3.13" / "site-packages"
        sp.mkdir(parents=True)
        (proj / ".pixi" / "envs" / "default" / "bin").mkdir(parents=True)
        (proj / ".pixi" / "envs" / "default" / "bin" / "python").touch()

        # Create a mock package inside the project's pixi site-packages
        mock_mod = sp / "zensus_custom_pkg.py"
        mock_mod.write_text("MAGIC_VALUE = 42\n", encoding="utf-8")

        # Package is not in sys.path yet
        self.assertNotIn(str(sp), sys.path)

        # Import dynamically using importlib
        mod = importlib.import_module("zensus_custom_pkg")
        self.assertEqual(getattr(mod, "MAGIC_VALUE"), 42)
        # Verify site-packages was auto-injected
        self.assertIn(str(sp), sys.path)
        self.assertEqual(os.environ.get("ASTROAI_MARIMO_PROJECT"), str(proj.resolve()))

    def test_use_project_missing_env_raises(self) -> None:
        proj = self.tmp_path / "bare"
        proj.mkdir()
        (proj / "pixi.toml").write_text("[workspace]\n", encoding="utf-8")
        with self.assertRaises(FileNotFoundError):
            use_project(proj)

    def test_install_package_requires_active_project(self) -> None:
        os.environ.pop("ASTROAI_MARIMO_PROJECT", None)
        with self.assertRaises(RuntimeError):
            install_package("rich")


if __name__ == "__main__":
    unittest.main()



