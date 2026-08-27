"""CANFAR session widget helpers for marimo notebooks.

Import this module inside a marimo cell::

    from canfar_marimo import file_browser, vospace_controls, project_env_controls

    fb = file_browser()
    fb  # last expression → display

    pe = project_env_controls()
    pe.panel  # pick a cloned project and activate its env

    from canfar_marimo import install_package
    install_package("rich")  # pixi/uv into active project — not system pip

    vc = vospace_controls()
    vc.panel  # display inputs + buttons

In a dependent cell, call ``vc.result_md()`` / ``pe.result_md()`` so UI reacts.

VOSpace list/download: ``vospace_controls()`` (needs ``canfar login``).
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from types import SimpleNamespace

_PROJECT_MARKERS = ("pixi.toml", "pyproject.toml", "environment.yml", ".git")
# Paths we last injected into sys.path / PATH (cleared on next use_project).
_ACTIVE_PATH_PREFIXES: list[str] = []
_ACTIVE_BIN: str | None = None


def _mo():
    import marimo as mo  # type: ignore

    return mo


def file_browser(initial_path: str = "/scratch", **kwargs: object) -> object:
    """Return a ``mo.ui.file_browser`` configured for CANFAR session storage.

    Navigation is unrestricted so users can reach ``/scratch``, ``$WORK``,
    ``/arc/home/*``, and ``/arc/projects/*``. Include the return value as the
    cell's last expression (or in a layout) so it renders.
    """
    mo = _mo()
    return mo.ui.file_browser(
        initial_path=initial_path,
        restrict_navigation=False,
        label="Browse session storage",
        **kwargs,
    )


def file_browser_tips() -> object:
    """Markdown tips for the file browser — use as the cell's last expression."""
    mo = _mo()
    return mo.md(
        """
**Tip:** Navigate to:

- `/scratch` — fast session SSD for data and caches
- `/arc/home/<you>` — persistent home (config, credentials)
- `/arc/projects/<group>` — persistent shared datasets
- `$WORK` (`$SCRATCH/src`) — session code workspace
"""
    )


def work_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("WORK", "").strip() or "/scratch/src")


def list_projects(root: pathlib.Path | None = None) -> list[pathlib.Path]:
    """Return project dirs under ``$WORK`` (from ``astroai clone`` / ``init``)."""
    root = root or work_dir()
    found: list[pathlib.Path] = []
    if not root.is_dir():
        return found
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name == "notebooks":
            continue
        if any((child / m).exists() for m in _PROJECT_MARKERS):
            found.append(child)
    return found


def project_env_python(project: pathlib.Path) -> pathlib.Path | None:
    """Return the project's pixi/uv Python, or None if the env is not installed."""
    project = pathlib.Path(project)
    # Allow passing the venv root itself (…/project/.venv).
    direct = project / "bin" / "python"
    if direct.is_file() and (project / "pyvenv.cfg").is_file():
        return direct
    pixi = project / ".pixi" / "envs" / "default" / "bin" / "python"
    if pixi.is_file():
        return pixi
    venv = project / ".venv" / "bin" / "python"
    if venv.is_file():
        return venv
    return None


def _normalize_project(project: pathlib.Path) -> pathlib.Path:
    """Map a venv root to its project dir when the user passes ``…/.venv``."""
    project = project.expanduser().resolve()
    if (project / "pyvenv.cfg").is_file() and (project / "bin" / "python").is_file():
        return project.parent
    return project


def project_kind(project: pathlib.Path) -> str:
    """Return ``pixi`` or ``uv`` for package installs into this project."""
    project = _normalize_project(pathlib.Path(project))
    if (project / "pixi.toml").is_file() or (project / ".pixi").is_dir():
        return "pixi"
    if (project / "pyproject.toml").is_file():
        text = (project / "pyproject.toml").read_text(encoding="utf-8", errors="ignore")
        if "[tool.pixi" in text:
            return "pixi"
        return "uv"
    return "pixi"


def _set_marimo_package_manager(manager: str) -> None:
    """Point ~/.marimo.toml Packages UI at pixi/uv (never system pip)."""
    cfg = pathlib.Path.home() / ".marimo.toml"
    if not cfg.is_file():
        cfg.write_text(
            f'# AstroAI marimo\n\n[package_management]\nmanager = "{manager}"\n',
            encoding="utf-8",
        )
        return
    text = cfg.read_text(encoding="utf-8")
    lines = text.splitlines()
    section_idx: int | None = None
    next_section_idx: int | None = None
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped == "[package_management]":
            section_idx = i
        elif section_idx is not None and stripped.startswith("[") and stripped.endswith("]"):
            next_section_idx = i
            break
    desired = f'manager = "{manager}"'
    if section_idx is not None:
        section_end = next_section_idx if next_section_idx is not None else len(lines)
        for i in range(section_idx + 1, section_end):
            left = lines[i].split("=", 1)[0].strip() if "=" in lines[i] else ""
            if left == "manager":
                lines[i] = desired
                cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return
        lines.insert(section_idx + 1, desired)
        cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        sep = "\n\n" if text.rstrip() else ""
        cfg.write_text(
            f"{text.rstrip()}{sep}[package_management]\n{desired}\n",
            encoding="utf-8",
        )


def _clear_previous_activation() -> None:
    """Drop site-packages / bin from the last use_project call."""
    global _ACTIVE_PATH_PREFIXES, _ACTIVE_BIN
    for p in _ACTIVE_PATH_PREFIXES:
        while p in sys.path:
            sys.path.remove(p)
    _ACTIVE_PATH_PREFIXES = []
    if _ACTIVE_BIN:
        parts = os.environ.get("PATH", "").split(":")
        os.environ["PATH"] = ":".join(p for p in parts if p != _ACTIVE_BIN)
        _ACTIVE_BIN = None


def use_project(project: str | pathlib.Path) -> str:
    """Activate a cloned project's pixi/uv env into this marimo process.

    Marimo has no Jupyter-style kernels — it uses the Python it was started
    with. This prepends the project's site-packages (and ``src/``) so imports
    resolve against that stack. Also ``chdir``s into the project and sets
    env vars so the Packages sidebar runs ``pixi add`` / ``uv add`` there
    (CANFAR has no root — system ``pip install`` cannot work).
    """
    global _ACTIVE_PATH_PREFIXES, _ACTIVE_BIN

    raw = pathlib.Path(project).expanduser().resolve()
    project = _normalize_project(raw)
    if not project.is_dir():
        raise FileNotFoundError(f"Not a project directory: {project}")
    py = project_env_python(raw if raw != project else project)
    if py is None:
        py = project_env_python(project)
    if py is None:
        raise FileNotFoundError(
            f"No .pixi or .venv under {project} — run `pixi install` or `uv sync` first "
            "(built-in terminal: Ctrl-`)."
        )

    ver = subprocess.check_output(
        [str(py), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
        text=True,
    ).strip()
    cur = f"{sys.version_info.major}.{sys.version_info.minor}"
    warn = ""
    if ver != cur:
        warn = (
            f" Warning: project Python {ver} ≠ marimo Python {cur}; "
            "pure-Python packages usually work, native wheels may not."
        )

    libs = subprocess.check_output(
        [
            str(py),
            "-c",
            "import sysconfig; "
            "print(sysconfig.get_path('purelib')); "
            "print(sysconfig.get_path('platlib'))",
        ],
        text=True,
    ).splitlines()
    prepend = [p for p in libs if p and pathlib.Path(p).is_dir()]
    # De-dupe while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for p in prepend:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    prepend = uniq
    src = project / "src"
    if src.is_dir():
        prepend.append(str(src))
    prepend.append(str(project))

    _clear_previous_activation()
    sys.path[:0] = prepend
    _ACTIVE_PATH_PREFIXES = list(prepend)

    env_root = py.parent.parent
    bin_dir = str(py.parent)
    kind = project_kind(project)
    os.environ["VIRTUAL_ENV"] = str(env_root)
    os.environ["ASTROAI_MARIMO_PROJECT"] = str(project)
    os.environ["ASTROAI_MARIMO_PM"] = kind
    # So marimo Packages → uv add targets this project (not system Python).
    os.environ["UV_PROJECT"] = str(project)
    os.environ["UV_PROJECT_ENVIRONMENT"] = str(env_root)
    path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{bin_dir}:{path}" if path else bin_dir
    _ACTIVE_BIN = bin_dir

    # pixi/uv discover the manifest from cwd — Packages UI runs in this process.
    try:
        os.chdir(project)
    except OSError as exc:
        warn += f" (chdir failed: {exc})"

    try:
        _set_marimo_package_manager(kind)
    except OSError:
        pass

    return (
        f"Using `{project.name}` → `{env_root}` [{kind}]. "
        f"Packages sidebar / `install_package()` write here (not the image).{warn}"
    )


def install_package(
    package: str,
    *,
    pypi: bool = True,
    upgrade: bool = False,
    project: str | pathlib.Path | None = None,
) -> str:
    """Install into the activated (or given) project with pixi/uv — no root needed.

    Prefer this (or the Packages sidebar after ``use_project``) over ``pip install``.
    """
    raw = package.strip()
    if not raw:
        raise ValueError("Empty package name")
    pkgs = raw.split()
    root = pathlib.Path(project).expanduser().resolve() if project else None
    if root is None:
        env_proj = os.environ.get("ASTROAI_MARIMO_PROJECT", "").strip()
        if not env_proj:
            raise RuntimeError(
                "No project activated. Call use_project(...) first — "
                "CANFAR cannot pip-install into the image Python (no root)."
            )
        root = pathlib.Path(env_proj)
    root = _normalize_project(root)
    if project_env_python(root) is None:
        raise FileNotFoundError(
            f"No env under {root} — run `pixi install` or `uv sync` first."
        )
    kind = project_kind(root)
    if kind == "pixi":
        # pixi has no -C; --manifest-path accepts the workspace directory.
        cmd = [
            "pixi",
            "upgrade" if upgrade else "add",
            "--manifest-path",
            str(root),
        ]
        if pypi:
            cmd.append("--pypi")
        cmd.extend(pkgs)
    else:
        cmd = ["uv", "add", "--project", str(root)]
        if upgrade:
            cmd.append("--upgrade")
        cmd.extend(pkgs)
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"{kind} not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"{' '.join(cmd)} failed:\n{err}") from exc
    use_project(root)
    tail = (proc.stdout or "").strip().splitlines()[-3:]
    note = ("\n" + "\n".join(tail)) if tail else ""
    return f"Installed {' '.join(pkgs)} into `{root.name}` via {kind}.{note}"


def project_env_controls() -> SimpleNamespace:
    """Dropdown + button to activate a project env cloned under ``$WORK``.

    Returns a namespace with ``panel`` (display) and ``result_md()`` (status).
    """
    mo = _mo()
    projects = list_projects()
    options = {p.name: str(p) for p in projects}
    picker = mo.ui.dropdown(
        options=options or {"(none yet)": ""},
        label="Project under $WORK",
        value=next(iter(options), "(none yet)"),
    )
    btn = mo.ui.button(label="Activate env", disabled=not options)
    header = mo.md(
        "Pick a project from `astroai clone` / `astroai init`, then activate "
        "its `.pixi` / `.venv`. That also aims the **Packages** sidebar at "
        "`pixi add` / `uv add` in the project (image `pip` has no write access). "
        "Shell: **Ctrl-`**."
        if options
        else "No projects under `$WORK` yet — open the **terminal** (Ctrl-`) and run "
        "`astroai clone owner/repo` or `astroai init mylab`, then `pixi install`."
    )
    panel = mo.vstack([header, picker, btn])

    def result_md() -> object:
        if not options:
            return mo.md("_Clone or init a project first._")
        if not btn.value:
            active = os.environ.get("ASTROAI_MARIMO_PROJECT", "")
            if active:
                return mo.md(f"Active: `{active}`")
            return mo.md("_Select a project and click **Activate env**._")
        target = picker.value
        if not target:
            return mo.md("**Pick a project first.**")
        try:
            msg = use_project(target)
            return mo.md(f"**{msg}**")
        except Exception as exc:  # noqa: BLE001
            return mo.md(f"**Error:** {exc}")

    return SimpleNamespace(
        projects=projects,
        picker=picker,
        btn=btn,
        panel=panel,
        result_md=result_md,
    )


def package_install_controls() -> SimpleNamespace:
    """Text + button to ``pixi add --pypi`` / ``uv add`` into the active project."""
    mo = _mo()
    pkg = mo.ui.text(label="Package(s)", placeholder="astropy pandas", full_width=True)
    btn = mo.ui.button(label="Install into active project")
    header = mo.md(
        "Installs with **pixi** / **uv** into the activated project under `$WORK` "
        "(same as the Packages sidebar after Activate). "
        "Do **not** use bare `pip install` — no root in CANFAR sessions."
    )
    panel = mo.vstack([header, pkg, btn])

    def result_md() -> object:
        if not btn.value:
            active = os.environ.get("ASTROAI_MARIMO_PROJECT", "")
            tip = f" Active: `{active}`." if active else " Activate a project first."
            return mo.md(f"_{tip}_")
        name = (pkg.value or "").strip()
        if not name:
            return mo.md("**Enter a package name.**")
        try:
            msg = install_package(name)
            return mo.md(f"**{msg}**")
        except Exception as exc:  # noqa: BLE001
            return mo.md(f"**Error:** {exc}")

    return SimpleNamespace(pkg=pkg, btn=btn, panel=panel, result_md=result_md)


def vospace_controls() -> SimpleNamespace:
    """Build Vault UI controls as marimo globals-friendly objects.

    Returns a namespace with:

    - ``panel`` — layout to display (inputs + buttons)
    - ``result_md()`` — markdown for the latest list/download action
    - ``available`` — whether ``vos`` imported
    - widget attrs: ``uri``, ``dest``, ``list_btn``, ``fetch_btn``
    """
    mo = _mo()
    vos_mod = None
    err = ""
    try:
        import vos as vos_mod
    except ImportError:
        err = (
            "`vos` module not found (expected in the Docker image). "
            "In the **terminal** (Ctrl-`): `uv pip install --system vos`"
        )

    available = vos_mod is not None
    uri = mo.ui.text(
        label="vos: URI",
        placeholder="vos:cadc.nrc.ca~vospace/your/path",
        full_width=True,
    )
    dest = mo.ui.text(label="Download to", value="/scratch")
    list_btn = mo.ui.button(label="List contents", disabled=not available)
    fetch_btn = mo.ui.button(label="Download file", disabled=not available)

    header = (
        mo.md(f"**Warning:** {err}")
        if err
        else mo.md(
            "Authenticate first: **terminal** (Ctrl-`) → `canfar login`, then list or download."
        )
    )
    panel = mo.vstack([header, uri, dest, mo.hstack([list_btn, fetch_btn])])

    def result_md() -> object:
        if not available or vos_mod is None:
            return mo.md("VOSpace client unavailable.")
        if list_btn.value and uri.value:
            try:
                client = vos_mod.Client()
                entries = client.listdir(uri.value)
                body = "\n".join(entries)
                return mo.md(f"```\nContents of {uri.value}:\n{body}\n```")
            except Exception as exc:  # noqa: BLE001
                return mo.md(f"**Error:** {exc}")
        if fetch_btn.value and uri.value:
            try:
                client = vos_mod.Client()
                fname = uri.value.rstrip("/").rsplit("/", 1)[-1]
                target = dest.value or "/scratch"
                client.copy(uri.value, f"{target}/{fname}")
                return mo.md(f"**Copied** `{uri.value}` → `{target}/{fname}`")
            except Exception as exc:  # noqa: BLE001
                return mo.md(f"**Error:** {exc}")
        return mo.md("_Enter a `vos:` URI and click **List contents** or **Download file**._")

    return SimpleNamespace(
        available=available,
        uri=uri,
        dest=dest,
        list_btn=list_btn,
        fetch_btn=fetch_btn,
        panel=panel,
        result_md=result_md,
    )


class VOSpaceUI:
    """Backward-compatible wrapper around :func:`vospace_controls`.

    Prefer ``vospace_controls()`` in new notebooks. Display ``.panel`` and call
    ``.result_md()`` from a dependent cell so button clicks stay reactive.
    """

    def __init__(self) -> None:
        self._vc = vospace_controls()
        self.available = self._vc.available
        self.msg = "" if self.available else "vos unavailable"
        self.uri = self._vc.uri
        self.dest = self._vc.dest
        self.list_btn = self._vc.list_btn
        self.fetch_btn = self._vc.fetch_btn
        self.panel = self._vc.panel

    def render(self) -> object:
        """Return the control panel (use as the cell's last expression)."""
        return self.panel

    def result_md(self) -> object:
        return self._vc.result_md()


__all__ = [
    "VOSpaceUI",
    "file_browser",
    "file_browser_tips",
    "install_package",
    "list_projects",
    "package_install_controls",
    "project_env_controls",
    "project_env_python",
    "project_kind",
    "use_project",
    "vospace_controls",
    "work_dir",
]
