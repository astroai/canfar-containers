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

### Coming from Jupyter?

- **No Run button** — marimo is always running. Edit a cell and dependents update.
- **`.py`, not `.ipynb`** — plain Python you can `git diff`.
- **Reactive** — change a variable and every cell that reads it re-runs.
- **Files** — use **Session Files** below, or **File → Open** (Cmd/Ctrl+O).
  Symlinks `📁_scratch`, `📁_work`, `📁_arc` sit next to this notebook.
- **Terminal** — open a **webterm** tab for `git`, `canfar login`, `vcp`, and
  mutating `astroai` commands (`init`, `save`, `agent install`).

### Quick rules

1. Keep notebooks under `$WORK/notebooks` (this directory).
2. Put big files on `$SCRATCH` or `/arc/projects` — never fill `/arc/home` with caches.
3. `$SCRATCH` is **session-private** — other sessions cannot see it; share via `/arc/projects` or home.
4. Before the session ends, save your environment and copy results to `/arc/projects` or `vos:`.

### Open an existing project

1. In a **webterm**: `astroai init mylab` or `astroai clone owner/repo`
   (projects land under `$WORK`).
2. Here: **File → Open** and browse into that folder, or follow the paths listed
   in **Session status** below.
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
        lines.append("- **projects** (File → Open):")
        for p in found:
            lines.append(f"  - `{p}`")
    else:
        lines.append(
            "- **projects**: none detected under work yet — "
            "`astroai init mylab` or `astroai clone owner/repo` in a webterm"
        )

    if notes:
        lines.extend(f"- {n}" for n in notes)

    mo.md("\n".join(lines))
    return (os, pathlib, scratch, subprocess, work)


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

**Interim:** use the controls below (or `vls` / `vcp` in a webterm).
Authenticate first: `canfar login` in a webterm.

Native marimo **Remote Storage** for Vault will land once the `vos` client
ships fsspec support — until then this is the in-notebook path.
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
Use `vls` / `vcp` in a **webterm** for VOSpace access.
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
### astroai (webterm)

Read-only checks run in **Session status** above. Mutating work stays in a
**webterm** tab:

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

**AI agents** (config on `/arc/home`)

```bash
astroai agent setup             # once per user (also seeds marimo AI)
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
**OpenRouter**, same as `astroai` agents.

1. Once: `astroai agent setup` in a webterm (stores the key on `/arc/home`).
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
- Or use a short-lived venv under `/scratch` if you must.
"""
    )
    return


if __name__ == "__main__":
    app.run()
