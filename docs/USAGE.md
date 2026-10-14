# Session user guide

How to use **AstroAI** session images on the
[CANFAR Science Platform](https://www.opencadc.org/canfar/).

This file ships inside images as `/opt/astroai/USAGE.md`.

| You want… | Read |
|-----------|------|
| This page | First session, storage, Ray, troubleshooting |
| `astroai` command detail | [astroai USAGE](https://github.com/astroai/canfar-lab/blob/main/docs/USAGE.md) · `canfar-lab help` |
| Ray operators | [RAY.md](RAY.md) |
| Platform CLI | [opencadc.github.io/canfar](https://opencadc.github.io/canfar/) |

## Scientist card

1. Portal → launch **studio** (coding portal) or **openresearch** (autoresearch) as home base (or terminal/vscode/notebook/marimo/ray-manager as needed).
2. Inside: `astroai` · `canfar-lab help` · `less /opt/astroai/USAGE.md`
3. Work under `$SRCDIR` (same as `$WORK`; `/scratch/src` on CANFAR so container OOM does not wipe it) and `/scratch` (data/caches).
4. Persist to `/arc/home` or `/arc/projects` before the session ends (`canfar-lab save` / `git push`).
5. Env snapshots live in `~/.astroai/lab/saves/` on `/arc/home` — resume them in the next session with `canfar-lab resume NAME`.

### Home base: AstroAI Studio (coding portal)

1. Launch **`studio`** with tag `26.10` / `latest`.
2. Open the connect URL — DeepSeek Harness (`dsh`) coding agent in the browser.
3. Top-right chips (same as openresearch):
   - **Terminal** → `/astroai-terminal/` (ghostty-web + tmux; **← Studio** returns)
   - **AstroAI** → `/astroai-agents/` — Install/Setup agents, **Start batch compute**
4. Skills: `npx skills add astroai/canfar-skills`. Team review: pick **AstroAI Studio Team** preset (or `canfar-lab panel run` headless).
5. Laptop: `canfar-lab studio` (same stack, no Skaha proxy).

```bash
canfar create --name studio contributed images.canfar.net/astroai/studio:26.10
```

### Alternate home base: AstroAI hub (openresearch)

1. Launch **`openresearch`** with tag `26.10` / `latest`.
2. Open the connect URL, then either:
   - click the blue **AstroAI** chip (top-right), or
   - append `/astroai-agents/` (e.g. `…/session/contrib/<id>/astroai-agents/`).
3. For a shell in the same session: click **Terminal** (left of AstroAI) or open
   `/astroai-terminal/` — ghostty-web + tmux. **← OpenResearch** returns to orx.
4. In the hub (one screen):
   - **Start batch compute** — autoscaling ray-manager, wires OpenResearch (when on openresearch)
   - Agent table — same columns as `canfar-lab agent list` (Agent, Bin, Cfg, Where, Ver). **Install** puts the CLI on `$SCRATCH/.local/bin`; **Setup** writes that agent's config, skills dirs, and default MCP/rules/tools on `/arc/home`. Skill packs: `npx skills add astroai/canfar-skills`
   - Status shows CANFAR auth, manager Running/Pending, wire state, Jobs URL
   - **← Back** returns to the main UI
5. Run experiments in OpenResearch — default compute is already CANFAR batch. Put shared I/O on `/arc` (`/scratch` is per-pod only).
6. Power users: `canfar-lab agent …` in the Terminal chip; cluster ops on ray-manager.

```bash
canfar login   # once, from terminal — persists under /arc/home
canfar create --name orx contributed images.canfar.net/astroai/openresearch:26.10
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

**Home quota %:** CANFAR homes use CephFS directory quotas (`ceph.quota.max_bytes`). `canfar-lab status` prefers those xattrs; `ceph.dir.rbytes` can lag a few seconds after large writes — that is Ceph MDS accounting, not a frozen UI cache. Refresh with `canfar-lab status`.

```bash
canfar-lab status
canfar data stage /arc/projects/mygroup/raw
canfar data sync /scratch/out /arc/projects/mygroup/out
```

---

## Ray (first-class)

**Preferred:** from openresearch, AstroAI hub → **Start batch compute**.
That launches an autoscaling **ray-manager** and wires OpenResearch. Jobs with
`--cpus` add workers.

Manual path: `canfar-lab cluster start`, or launch
**ray-manager** from the portal and open Connect URL.

```bash
# AstroAI hub → Start batch compute
# or:
canfar create --name astroai-compute --cpu 2 --memory 8 contributed images.canfar.net/astroai/ray-manager:26.09
# or: canfar-lab cluster start
canfar-lab run train.py --cpus 2 --memory 8GiB
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
canfar-lab init mylab          # or clone owner/repo
canfar-lab save mylab
canfar-lab resume mylab --yes
canfar-lab cluster start
canfar-lab run train.py --cpus 2
canfar-lab agent setup         # once (UI sessions auto-run in background; terminal opt-in)
canfar-lab agent install claude
canfar-lab kernel ensure       # notebook
```

Compilers and editors are in interactive images; put CUDA/ML stacks in your pixi/uv project locks.

---

## Session notes

| Image | Notes |
|-------|-------|
| `terminal` | ghostty-web + tmux on `:5000` |
| `vscode` | OpenVSCode on `:5000` |
| `marimo` | Reactive `.py` notebooks; starter seeded once under `$SRCDIR/notebooks` |
| `notebook` | JupyterLab `:8888`. Stock Skaha may run platform Jupyter CMD — AstroAI `startup-notebook.sh` only with a platform override ([OPERATORS.md](OPERATORS.md)) |
| `openresearch` | Autoresearch UI (`orx`) on `:5000`; **Terminal** chip → `/astroai-terminal/` (ghostty-web); AstroAI hub at `/astroai-agents/` |
| `openscience` | OpenScience research workspace on `:5000` with the astroai CADC/VO tools and astronomy Python; **AstroAI** chip → hub at `/astroai-agents/` (model keys, compute) |
| `studio` | dsh coding portal on `:5000`; **Terminal** + **AstroAI** chips — see [STUDIO.md](STUDIO.md) |
| `ray-manager` | Cluster UI + Ray head; see Ray section |
| `improc` | Headless FITS/HDF5 image-processing CLIs — see [Image processing (`improc`)](#image-processing-improc) |
| `improc-terminal` | Same tools + browser terminal (ghostty-web/tmux) |
| `improc-notebook` | Same tools + JupyterLab (default kernel = science venv) |

CADC clients (`cadcget`, `vls`, …) are on PATH from `/opt/canfar`.

---

## Image processing (`improc`)

| Image | Use |
|-------|-----|
| `improc` | Headless batch |
| `improc-terminal` | Interactive CLI (Contributed, :5000) |
| `improc-notebook` | JupyterLab (Notebook, :8888); kernel **Python 3 (improc)** has healpy/galsim/… |

PATH includes `/opt/astroai/venv/improc/bin` and sourcextractor++.

| Area | Tools |
|------|--------|
| Detection / catalog | `source-extractor` (`sextractor`), sourcextractor++, `scamp`, `tractor`, IRAF |
| Deblending / scene modeling | `scarlet`, `scarlet2` (JAX) |
| Simulation | `skymaker`, `stuff` |
| Cosmic rays / clean | `astroscrappy`, `lacosmic`, `ccdproc` helpers |
| Contaminant masks | `maximask`, `maxitrack` (same science venv; pulls TensorFlow) |
| DIA (difference imaging) | **`sfft`**, `zogyp` (modern; not HOTPANTS) |
| Mask / weight | `weightmask`, `weightwatcher`, `missfits`, gnuastro `astnoisechisel` / `astsegment` |
| Astrometry / WCS | `twirl`, `astrometry.net`, `tweakwcs` |
| PSF | `psfex`, `piff`, gnuastro `astscript-psf-*`, `galfit`, `imfit` |
| Morphology / galaxy fitting | `statmorph`, `petrofit`, `galight` (+ `lenstronomy`) |
| Mosaic / coadd | `swarp`, `montage`, `theli`, `reproject` |
| Spherical / HEALPix | `healpy`, `healsparse`, `astropy-healpix`, `mocpy`, `hpgeom` |
| General imaging / archives | `scikit-image`, `opencv` (`cv2`), `astroquery`, `montage-wrapper` |
| Pretty pictures | `stiff`, `fitspng`, `fitscut`, `astconvertt`, ImageMagick |
| FITS / HDF5 / tables | cfitsio utils, `fitsverify`, `topcat`/`stilts`, `pqrs`, `h5dump`, `torchfits` (FITS↔tensor; CUDA torch for GPU reads) |

Science Python (including MaxiMask/TensorFlow) lives in `/opt/astroai/venv/improc`
(on PATH). Conda-only tools use separate prefixes under `/opt/astroai/conda/`
(sxpp, skymaker, ngmix); their CLIs are linked into `/usr/local/bin`. Import
ngmix with `/opt/astroai/conda/ngmix/bin/python`.

A complete Stuff → SkyMaker → SExtractor simulation workflow (generate a
synthetic galaxy field, render it, extract sources) is in
`examples/improc/simulate_field.sh` — see `examples/improc/README.md`.

---

## Spectroscopy (`specproc`)

Group image, not part of the public `astroai` catalog. A CANFAR account does
not hide it: the public project allows anonymous pull. Push only to the
group Harbor project (`OWNER=<group> make push-specproc`). The image includes
pPXF (no redistribution) and MOOG (no redistribution grant).

| Image | Use |
|-------|-----|
| `specproc` | Headless batch |
| `specproc-terminal` | Interactive CLI (Contributed, :5000) |
| `specproc-notebook` | JupyterLab (Notebook, :8888); kernel **Python 3 (specproc)** |

Input is a reduced 1D spectrum. No instrument pipelines.

| Area | Tools |
|------|--------|
| Stellar atmospheres | Julia `Korg` (+ `Korg.Fit`), Turbospectrum NLTE (`babsma_lu`, `bsyn_lu`), TSFitPy, `synspec` / `synple`, `moogpy`, `pymoog`, pyKurucz (`/opt/astroai/pykurucz`), iNNterpol (`/opt/astroai/iNNterpol`) |
| Population / SED | FSPS (`SPS_HOME=/opt/astroai/fsps`), `astro-prospector`, `astro-sedpy`, `astroARIADNE` |
| Fitting | `pysme-astro`, pPXF, FERRE (`ferre`) |
| Spectrum Python | `specutils`, `specreduce`, `pyspeckit`, `spectres`, `lmfit` |
| Nidever | `thedoppler`, `annieslasso` (`thecannon`), `fraunhofer`, `roland`, `starlyte` |
| Dynamics / chemical evolution | Agama, VICE |

The 2009 autoMOOG program has no public source. The image installs `pymoog`
and keeps the MOOG binary at `/opt/astroai/pymoog-home/.pymoog`. Login shells
symlink `$HOME/.pymoog` there when the user does not already have one.
pymoog's line lists and model grid are the mount, not that tree.

Line lists, atmosphere grids, FSPS libraries, and filter catalogs are one
readonly tree, mounted at `/specproc-data` (`SPECPROC_DATA`). The image
symlinks each package at that tree. Packages that read the same files share
one directory: `synspec` and `synple` both use `linelists/synspec/`. Products
that only share a name (FSPS MIST isochrones, the `isochrones` package's MIST
tables, raw MARCS, Korg's HDF5 atmospheres, pymoog's model pickles) sit next
to each other so a second copy is not required.

```text
/specproc-data/
  linelists/synspec/     gfATO.19.11  gfMOLsun.20.11  gfTiO.20.11  H2O-8.20.11
  linelists/pymoog/      pymoog VALD and Kurucz lists
  linelists/kurucz/      gfallvac.latest
  atmospheres/korg/      SDSS_MARCS_atmospheres.h5
                         resampled_cool_dwarf_atmospheres.h5
                         MARCS_metal_poor_atmospheres.h5
  atmospheres/marcs/     raw MARCS models (Turbospectrum, TSFitPy)
  atmospheres/pymoog/    pymoog model grid
  atmospheres/nlte/      Turbospectrum NLTE departure grids
  fsps/SPECTRA/          C3K, MILES, and the other FSPS libraries
  fsps/ISOCHRONES/       MIST and the other FSPS isochrones
  fsps/nebular/
  fsps/dust/
  filters/sedpy/
  filters/pyphot/        new_filters.hd5  synphot_nonhst.hd5
  models/ariadne/grids/  ARIADNE grid index
  models/ariadne/spectra/  PHOENIX, BT-Settl, Kurucz spectra (ARIADNE_MODELS)
  isochrones/            MIST bolometric corrections (ISOCHRONES)
  dustmaps/              dustmaps data_dir
  grids/ferre/           FERRE model grids
```

Build the shared synspec/synple lists on the mount with
`/opt/astroai/share/specproc/synspec-linelists.mk` (it fetches
`gfATO.19`, `gfMOLsun.20`, `gfTiO.20`, and `H2O-8.20` and runs `list2bin`).
Korg's built-in line lists stay in the Julia package. Its atmosphere HDF5
files do not, and Korg opens them while the package loads, so `using Korg`
needs `atmospheres/korg/` on the mount. The first successful load compiles
into `$SCRATCH/julia-compile` (or `/tmp/julia-compile`); that cache is not
in the image.

pyKurucz ships the kurucz-a1 emulator weights. `lines/gfallvac.latest` points
at `linelists/kurucz/gfallvac.latest`. The ~5 GB molecular / `gfpred` set is
not in the image. Copy the checkout onto a writable disk, then:

```bash
git lfs pull --include="lines/gfallvac.latest"
python scripts/download_data.py
```

Pass that catalog path to `synthe_py`. iNNterpol reads its weights from the
working directory: `cd /opt/astroai/iNNterpol` for ATLAS9, or
`iNNterpol_MARCS` / `iNNterpol_PHOENIX` for the other grids.

`fetch_spectra_cache` writes into the image venv, which session users cannot
update. Put the ARIADNE spectra on the mount instead.
`pyrre` and CPU torch are not installed. iNNterpol and `starlyte`'s FERRE
emulator import torch when they run; install torch in the session if you need
them. The iNNterpol weights are in the checkout.

Agama is compiled into the image (actions, Schwarzschild models, self-consistent
galaxies). Its sources are BSD/MIT; the GSL-linked library is GPL. VICE is the
chemical-evolution integrator. galpy and gala are ordinary wheels and are not
baked in.

```bash
export SPS_HOME=/opt/astroai/fsps
export SPECPROC_DATA=/specproc-data
export JULIA_PROJECT=/opt/astroai/julia/korg
export TURBOSPECTRUM_ROOT=/opt/astroai/Turbospectrum_NLTE
export TSFITPY_ROOT=/opt/astroai/TSFitPy
```

---

## Diagnostics / troubleshooting

```bash
canfar-lab status --json
```

| Symptom | Action |
|---------|--------|
| Other session missing `/scratch` files | Expected — scratch is session-private; use `/arc/projects` or `canfar data` |
| Lost files after session end | Persist to `/arc` next time (`canfar-lab save` / `git push` / `canfar data`) |
| Home quota full | `canfar-lab status` (quota %) — prune caches under `/scratch` manually |
| Session stuck **Pending** | `canfar ps` / events; contributed quota ≈3; headless Pending is often a Skaha flake ([OPERATORS](OPERATORS.md#platform-notes-headless-pending)) |
| Session **Failed** / UI never opens | `canfar logs <id>` — grep `[astroai-boot]`; also `~/.astroai/lab/boot.log` on home |

---

## Related

- [canfar-lab](https://github.com/astroai/canfar-lab) — `astroai` CLI (`cluster` / `run` / `save` / `agent`)
- [OPERATORS.md](OPERATORS.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [RAY.md](RAY.md)
