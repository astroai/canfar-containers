"""AstroAI starter notebook for marimo sessions.

Canonical copy — keep containers in sync:
  make -C ../canfar-containers sync-marimo-starter

Keep code under $WORK (this folder). Put large data on $SCRATCH.
"""

import marimo  # type: ignore

__generated_with = "0.13.0"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo  # type: ignore

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
# AstroAI starter (marimo)

Welcome. Marimo notebooks are plain **`.py` files** — easy to git and review.

### Terminal (right here)

Built-in shell: press **Ctrl-`** (backtick), or open the footer **Developer**
panel (**Ctrl/Cmd-J**) → **Terminal**. Use it for `astroai clone`, `pixi install`,
`canfar login`, `git`, … A separate **webterm** session also works if you prefer.

### Coming from Jupyter?

- **No Run button** — marimo is always running. Edit a cell and dependents update.
- **`.py`, not `.ipynb`** — plain Python you can `git diff`.
- **Reactive** — change a variable and every cell that reads it re-runs.
- **Files** — use **Session Files** below, or **File → Open** (Cmd/Ctrl+O).
  Symlinks `📁_scratch`, `📁_work`, `📁_arc` sit next to this notebook.

### Quick rules

1. Keep notebooks under `$WORK/notebooks` (this directory).
2. Put big files on `$SCRATCH` or `/arc/projects` — never fill `/arc/home` with caches.
3. `$SCRATCH` is **session-private** — other sessions cannot see it; share via `/arc/projects` or home.
4. Before the session ends, save your environment and copy results to `/arc/projects` or `vos:`.

### Open an existing project

1. In the **terminal** (Ctrl-`): `astroai init mylab` or `astroai clone owner/repo`
   (projects land under `$WORK`).
2. Activate that project's env with **Project environment** below (or
   `from canfar_marimo import use_project; use_project("…")`).
3. **File → Open** to edit notebooks inside the project folder.
"""
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("### Session status")
    return


@app.cell(hide_code=True)
def _(mo):
    import json
    import os
    import pathlib
    import subprocess

    notes: list[str] = []

    # Apply scratch-backed caches even if the session missed profile hooks.
    try:
        out = subprocess.check_output(["astroai", "env", "export"], text=True)
        for line in out.splitlines():
            if line.startswith("export ") and "=" in line:
                body = line[len("export ") :]
                k, _, v = body.partition("=")
                os.environ[k] = v.strip().strip("'\"")
    except Exception as exc:  # noqa: BLE001 — show in notebook, don't crash
        notes.append(f"`env export` skipped: `{exc}`")

    scratch = pathlib.Path(os.environ.get("SCRATCH", "").strip() or "/scratch")
    work = pathlib.Path(os.environ.get("WORK", "").strip() or "/scratch/src")

    lines = [
        f"- **work** (`WORK`): `{work}`",
        f"- **scratch**: `{scratch}` "
        f"({'writable' if scratch.is_dir() and os.access(scratch, os.W_OK) else 'not writable'})",
        f"- **home** (keep tiny): `{pathlib.Path.home()}`",
        f"- **XDG_CACHE_HOME**: `{os.environ.get('XDG_CACHE_HOME', '(unset)')}`",
        f"- **OpenRouter key**: "
        f"{'set (`OPENROUTER_API_KEY` / `~/.astroai/lab/.env`)' if os.environ.get('OPENROUTER_API_KEY') or (pathlib.Path.home() / '.astroai' / 'lab' / '.env').is_file() else 'missing — once: `export OPENROUTER_API_KEY=…` then `astroai agent setup marimo`'}",
    ]

    # Banner JSON shows session paths and save count.
    try:
        proc = subprocess.run(
            ["astroai", "--json"],
            check=False,
            capture_output=True,
            text=True,
        )
        raw = (proc.stdout or "").strip()
        if raw:
            banner = json.loads(raw)
            lines.append(f"- **saves**: {banner.get('saves_count', '?')}")
        else:
            err = (proc.stderr or "").strip() or f"exit {proc.returncode}"
            lines.append(f"- **astroai**: no output (`{err}`)")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"- **astroai**: skipped (`{exc}`)")

    # Surface existing projects under the session work root.
    markers = ("pyproject.toml", "pixi.toml", "environment.yml", ".git")
    found: list[pathlib.Path] = []
    if work.is_dir():
        for child in sorted(work.iterdir()):
            if not child.is_dir() or child.name.startswith(".") or child.name == "notebooks":
                continue
            if any((child / m).exists() for m in markers):
                found.append(child)
    if found:
        lines.append("- **projects** (activate below / File → Open):")
        for p in found:
            lines.append(f"  - `{p}`")
    else:
        lines.append(
            "- **projects**: none detected under work yet — "
            "`astroai init mylab` or `astroai clone owner/repo` in the terminal (Ctrl-`)"
        )

    if notes:
        lines.extend(f"- {n}" for n in notes)

    mo.md("\n".join(lines))
    return (os, pathlib, scratch, subprocess, work)


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
### Project environment

Marimo has no Jupyter kernels — activate a cloned project's `.pixi` / `.venv`
here so notebook imports use that stack.

**Packages sidebar:** after Activate, installs go via **pixi** / **uv** into
that project. Bare `pip` into the image Python fails (no root on CANFAR).
"""
    )
    return


@app.cell(hide_code=True)
def _(mo):
    try:
        from canfar_marimo import project_env_controls  # type: ignore

        pe = project_env_controls()
        pe_picker = pe.picker
        pe_btn = pe.btn
        pe.panel
    except ImportError:
        pe = None
        pe_picker = None
        pe_btn = None
        mo.md(
            "`canfar_marimo` missing (expected in the Docker image). "
            "In the terminal: `cd $WORK/<project> && pixi shell` then restart marimo, "
            "or `from canfar_marimo import use_project` once the image is current."
        )
    return (pe, pe_picker, pe_btn)


@app.cell(hide_code=True)
def _(mo, pe, pe_btn, pe_picker):
    if pe is None or pe_btn is None or pe_picker is None:
        out = mo.md("")
    else:
        _ = (pe_picker.value, pe_btn.value)
        out = pe.result_md()
    out
    return


@app.cell(hide_code=True)
def _(mo):
    try:
        from canfar_marimo import package_install_controls  # type: ignore

        pi = package_install_controls()
        pi_pkg = pi.pkg
        pi_btn = pi.btn
        pi.panel
    except ImportError:
        pi = None
        pi_pkg = None
        pi_btn = None
        mo.md("")
    return (pi, pi_pkg, pi_btn)


@app.cell(hide_code=True)
def _(mo, pi, pi_btn, pi_pkg):
    if pi is None or pi_btn is None or pi_pkg is None:
        out = mo.md("")
    else:
        _ = (pi_pkg.value, pi_btn.value)
        out = pi.result_md()
    out
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("### Session Files")
    return


@app.cell(hide_code=True)
def _():
    try:
        from canfar_marimo import file_browser  # type: ignore

        fb = file_browser()
    except ImportError:
        import marimo as mo  # type: ignore

        fb = mo.ui.file_browser(
            initial_path="/scratch",
            restrict_navigation=False,
            label="Browse session storage",
        )
    fb
    return (fb,)


@app.cell(hide_code=True)
def _(fb, mo):
    try:
        from canfar_marimo import file_browser_tips as _fb_tips  # type: ignore
    except ImportError:

        def _fb_tips():
            return mo.md(
                """
**Tip:** Navigate to:

- `/scratch` — fast session SSD for data and caches
- `/arc/home/<you>` — persistent home (config, credentials)
- `/arc/projects/<group>` — persistent shared datasets
- `$WORK` — session code workspace (`/scratch/src` on CANFAR)

Selected paths from the browser appear here.
"""
            )

    paths = fb.value
    if not paths:
        out = _fb_tips()
    else:
        selected = "\n".join(f"- `{p}`" for p in paths)
        out = mo.md(f"**Selected:**\n{selected}")
    out
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
### CANFAR Vault (VOSpace)

Authenticate first: `canfar login` in the terminal (**Ctrl-`**), then list or
download below. Shell alternatives: `vls` / `vcp`.

(Marimo **Remote Storage** for Vault waits on a PyPI `canfar` release with
`vosfs` / fsspec.)
"""
    )
    return


@app.cell(hide_code=True)
def _(mo):
    # Bind widgets to cell globals so button clicks re-run the result cell.
    try:
        from canfar_marimo import vospace_controls  # type: ignore

        vc = vospace_controls()
        vos_uri = vc.uri
        vos_dest = vc.dest
        vos_list_btn = vc.list_btn
        vos_fetch_btn = vc.fetch_btn
        vc.panel
    except ImportError:
        vc = None
        vos_uri = None
        vos_dest = None
        vos_list_btn = None
        vos_fetch_btn = None
        mo.md(
            """
`canfar_marimo` is not available (expected inside the Docker image).
Use `vls` / `vcp` in the terminal for VOSpace access.
"""
        )
    return (vc, vos_uri, vos_dest, vos_list_btn, vos_fetch_btn)


@app.cell(hide_code=True)
def _(mo, vc, vos_dest, vos_fetch_btn, vos_list_btn, vos_uri):
    if (
        vc is None
        or vos_uri is None
        or vos_dest is None
        or vos_list_btn is None
        or vos_fetch_btn is None
    ):
        out = mo.md("")
    else:
        # Touch globals so marimo re-runs this cell on interaction.
        _ = (vos_uri.value, vos_dest.value, vos_list_btn.value, vos_fetch_btn.value)
        out = vc.result_md()
    out
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
### astroai (terminal)

Read-only checks run in **Session status** above. Mutating work stays in the
**built-in terminal** (Ctrl-`):

**First session / new project**

```bash
astroai init mylab              # pixi (recommended)
astroai init mylab --uv
astroai clone owner/repo
astroai clone owner/repo --from-env
```

**Persist before logout**

```bash
astroai save
# copy results to /arc/projects or vos: with canfar data / vcp
```

**AI agents** (one OpenRouter key on `/arc/home`)

```bash
# once per user — stores key in ~/.astroai/lab/.env for marimo + agents
export OPENROUTER_API_KEY=sk-or-v1-…
astroai agent setup             # seeds marimo AI + agent configs
astroai agent install kilo      # or goose, claude, opencode, codex, qoder
astroai agent update
```

Full reference: `astroai help` · [astroai docs](https://github.com/astroai/canfar-lab)
"""
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
### Marimo AI Assistant

Toolbar **AI** (or Cmd/Ctrl+Shift+E to refactor the current cell). Uses
**OpenRouter**, same key as `astroai` agents (`~/.astroai/lab/.env` →
`OPENROUTER_API_KEY`). You should not need to paste the key again into marimo.

1. Once: `export OPENROUTER_API_KEY=…` then `astroai agent setup` (or `… marimo`).
2. Open the AI sidebar; chat, agent mode, or generate cells from a prompt.
3. Pass in-memory values with `@variable_name`. Models: `~/.marimo.toml`.
"""
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
## Next steps

- Install packages into a **project** (`astroai init mylab`), not `$HOME`.
- Activate that env with **Project environment** above.
- Or use a short-lived venv under `/scratch` if you must.
"""
    )
    return


if __name__ == "__main__":
    app.run()
