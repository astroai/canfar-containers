# Session user guide

How to use **AstroAI** session images on the
[CANFAR Science Platform](https://www.opencadc.org/canfar/).

This file ships inside images as `/opt/astroai/USAGE.md`.

| You want… | Read |
|-----------|------|
| This page | First session, storage, Ray, troubleshooting |
| `astroai` command detail | [astroai USAGE](https://github.com/astroai/canfar-lab/blob/main/docs/USAGE.md) · `astroai help` |
| Ray operators | [RAY.md](RAY.md) |
| Platform CLI | [opencadc.github.io/canfar](https://opencadc.github.io/canfar/) |

## Scientist card

1. Portal → launch **openresearch** as your day-to-day home base (or webterm/vscode/notebook/marimo/ray-manager as needed).
2. Inside: `astroai` · `astroai help` · `less /opt/astroai/USAGE.md`
3. Work under `$SRCDIR` (same as `$WORK`; `/scratch/src` on CANFAR so container OOM does not wipe it) and `/scratch` (data/caches).
4. Persist to `/arc/home` or `/arc/projects` before the session ends (`astroai save` / `git push`).
5. Env snapshots live in `~/.astroai/lab/saves/` on `/arc/home` — resume them in the next session with `astroai resume NAME`.

### Home base: AstroAI hub (openresearch)

1. Launch **`openresearch`** with tag `26.09` / `latest`.
2. Open the connect URL, then either:
   - click the blue **AstroAI** chip (top-right), or
   - append `/astroai-agents/` (e.g. `…/session/contrib/<id>/astroai-agents/`).
3. In the hub (one screen):
   - **Start batch compute** — autoscaling ray-manager, wires OpenResearch (when on openresearch)
   - Agent table — same columns as `astroai agent list` (Agent, Bin, Cfg, Where, Ver). **Install** puts the CLI on PATH; **Setup** writes that agent's config, skills dirs, and default MCP/rules/tools on `/arc/home`. Skill packs: `npx skills add astroai/canfar-skills`
   - Status shows CANFAR auth, manager Running/Pending, wire state, Jobs URL
   - **← Back** returns to the main UI
4. Run experiments in OpenResearch — default compute is already CANFAR batch. Put shared I/O on `/arc` (`/scratch` is per-pod only).
5. Power users: `astroai agent …` in webterm; cluster ops on ray-manager.

```bash
canfar login   # once, from webterm — persists under /arc/home
canfar create --name orx contributed images.canfar.net/astroai/openresearch:26.09
canfar open <session-id>
# Hub: …/astroai-agents/ → Start batch compute
```

---

## Storage (remember scratch)

| Tier | Path | Lifetime | Shared across sessions? |
|------|------|----------|-------------------------|
| Source | `$SRCDIR` (`$WORK`, `/scratch/src`) | Session (survives container OOM) | No |
| Scratch | `/scratch` (`SCRATCH`) | Session | **No** — other sessions cannot see it |
| Home | `/arc/home/<you>` | Persistent | **Yes** |
| Projects | `/arc/projects/<group>` | Persistent | **Yes** (group ACLs) |

`/scratch` is fast and private to **this** session. Use `/arc/projects/…` (or home) when another session needs the same files live; move with `canfar data` (platform archive I/O).

**Home quota %:** CANFAR homes use CephFS directory quotas (`ceph.quota.max_bytes`). `astroai status` prefers those xattrs; `ceph.dir.rbytes` can lag a few seconds after large writes — that is Ceph MDS accounting, not a frozen UI cache. Refresh with `astroai status`.

```bash
astroai status
canfar data stage /arc/projects/mygroup/raw
canfar data sync /scratch/out /arc/projects/mygroup/out
```

---

## Ray (first-class)

**Preferred:** from openresearch, AstroAI hub → **Start batch compute**.
That launches an autoscaling **ray-manager** and wires OpenResearch. Jobs with
`--cpus` add workers.

Manual path: `astroai cluster start`, or launch
**ray-manager** from the portal and open Connect URL.

```bash
# AstroAI hub → Start batch compute
# or:
canfar create --name astroai-compute --cpu 2 --memory 8 contributed images.canfar.net/astroai/ray-manager:26.09
# or: astroai cluster start
astroai run train.py --cpus 2 --memory 8GiB
```

Dashboard: `connectURL/dashboard/`. Full detail: [RAY.md](RAY.md). Prefer manager memory **≥8 GiB**.

### OpenResearch → Ray (`orx exp run --backend ray`)

`openresearch` defaults compute to CANFAR batch (Ray Jobs under the hood). Preferred path:

1. AstroAI hub → **Start batch compute** — autoscaling manager, wires Settings.
2. Set agent API keys in agent configs / OpenResearch settings (not in the hub).
3. Run experiments in OpenResearch (no `--backend` needed once defaulted).

Manual fallback: Settings → Compute → Ray, then **Start batch compute**. Cluster lifecycle stays on that button / the AstroAI hub, not a CANFAR card in upstream OpenResearch.

Put env saves on `/arc` (`~/.astroai/lab/saves/` or `/arc/projects/<group>/env-saves/` via `save --to` / `resume --from`). Ray workers join with the image venv; restore a save in an interactive session if you need that stack on `$SRCDIR`.

---

## Everyday `astroai`

```bash
astroai init mylab          # or clone owner/repo
astroai save mylab
astroai resume mylab --yes
astroai cluster start
astroai run train.py --cpus 2
astroai agent setup         # once (UI sessions auto-run in background; webterm opt-in)
astroai agent install claude
astroai kernel ensure       # notebook
```

Compilers and editors are in interactive images; put CUDA/ML stacks in your pixi/uv project locks.

---

## Session notes

| Image | Notes |
|-------|-------|
| `webterm` | ttyd + tmux on `:5000` |
| `ghostty-web` | ghostty-web + tmux on `:5000` |
| `vscode` | OpenVSCode on `:5000` |
| `marimo` | Reactive `.py` notebooks; starter seeded once under `$SRCDIR/notebooks` |
| `notebook` | JupyterLab `:8888`. Stock Skaha may run platform Jupyter CMD — AstroAI `startup-notebook.sh` only with a platform override ([OPERATORS.md](OPERATORS.md)) |
| `openresearch` | Autoresearch UI (`orx`) on `:5000`; AstroAI hub at `/astroai-agents/` (batch compute + agent list) |
| `ray-manager` | Cluster UI + Ray head; see Ray section |
| `improc` | Headless FITS/HDF5 image-processing CLIs — see [Image processing (`improc`)](#image-processing-improc) |
| `improc-webterm` | Same tools + browser terminal (ttyd/tmux) |
| `improc-notebook` | Same tools + JupyterLab (default kernel = science venv) |

CADC clients (`cadcget`, `vls`, …) are on PATH from `/opt/astroai/venv/cadc`.

---

## Image processing (`improc`)

| Image | Use |
|-------|-----|
| `improc` | Headless batch |
| `improc-webterm` | Interactive CLI (Contributed, :5000) |
| `improc-notebook` | JupyterLab (Notebook, :8888); kernel **Python 3 (improc)** has healpy/galsim/… |

PATH includes `/opt/astroai/venv/improc/bin` and sourcextractor++.

| Area | Tools |
|------|--------|
| Detection / catalog | `source-extractor` (`sextractor`), sourcextractor++, `scamp` (2.15 from sid), `tractor`, IRAF |
| Deblending / scene modeling | `scarlet`, `scarlet2` (JAX) |
| Simulation | `skymaker`, `stuff` |
| Cosmic rays / clean | `astroscrappy`, `lacosmic`, `ccdproc` helpers |
| Contaminant masks | `maximask`, `maxitrack` (own TF venv — not mixed with science Python) |
| DIA (difference imaging) | **`sfft`**, `zogyp` (modern; not HOTPANTS) |
| Mask / weight | `weightwatcher`, `missfits`, gnuastro `astnoisechisel` / `astsegment` |
| Astrometry / WCS | `twirl`, `astrometry.net`, `tweakwcs` |
| PSF | `psfex`, `piff`, gnuastro `astscript-psf-*`, `galfit`, `imfit` |
| Morphology / galaxy fitting | `statmorph`, `petrofit`, `galight` (+ `lenstronomy`) |
| Mosaic / coadd | `swarp`, `montage`, `theli`, `reproject` |
| Spherical / HEALPix | `healpy`, `healsparse`, `astropy-healpix`, `mocpy`, `hpgeom` |
| General imaging / archives | `scikit-image`, `opencv` (`cv2`), `astroquery`, `montage-wrapper` |
| Pretty pictures | `stiff`, `fitspng`, `fitscut`, `astconvertt`, ImageMagick |
| FITS / HDF5 / tables | cfitsio utils, `fitsverify`, `topcat`/`stilts`, `pqrs`, `h5dump`, `torchfits` 1.0 (FITS↔tensor; CUDA 12.9 torch for GPU reads) |

Science Python lives in `/opt/astroai/venv/improc` (on PATH). MaxiMask uses a
**separate** `/opt/astroai/venv/maximask` so TensorFlow cannot conflict with
GalSim/numba; only the `maximask` / `maxitrack` wrappers are on PATH. `ngmix`
(Sheldon's Gaussian-mixture image/shape tools) has no PyPI release, so it lives
in its own conda env at `/opt/astroai/conda/ngmix` — use
`/opt/astroai/conda/ngmix/bin/python` to import it.

A complete Stuff → SkyMaker → SExtractor simulation workflow (generate a
synthetic galaxy field, render it, extract sources) is in
`examples/improc/simulate_field.sh` — see `examples/improc/README.md`.

---

## Diagnostics / troubleshooting

```bash
astroai status --json
```

| Symptom | Action |
|---------|--------|
| Other session missing `/scratch` files | Expected — scratch is session-private; use `/arc/projects` or `canfar data` |
| Lost files after session end | Persist to `/arc` next time (`astroai save` / `git push` / `canfar data`) |
| Home quota full | `astroai status` (quota %) — prune caches under `/scratch` manually |
| Session stuck **Pending** | `canfar ps` / events; contributed quota ≈3; headless Pending is often a Skaha flake ([OPERATORS](OPERATORS.md#platform-notes-headless-pending)) |
| Session **Failed** / UI never opens | `canfar logs <id>` — grep `[astroai-boot]`; also `~/.astroai/lab/boot.log` on home |

---

## Related

- [canfar-lab](https://github.com/astroai/canfar-lab) — `astroai` CLI (`cluster` / `run` / `save` / `agent`)
- [OPERATORS.md](OPERATORS.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [RAY.md](RAY.md)
