# Contributing

Thanks for helping improve AstroAI session images. Contributions are welcome via
GitHub pull requests — docs, scripts, Dockerfiles, and config.

Licensed under [BSD-2-Clause](../LICENSE).

## Documentation map

| Doc | Audience |
|-----|----------|
| [USAGE.md](USAGE.md) | Session users |
| **CONTRIBUTING.md** (this file) | Developers changing this repo |
| [OPERATORS.md](OPERATORS.md) | Maintainers — push / register / smoke |
| [RAY.md](RAY.md) | Ray manager + workers |
| [README.md](../README.md) | Overview and make targets |

In a session: `less /opt/astroai/USAGE.md`.

## Get the repo

```bash
gh auth login
gh repo clone astroai/canfar-containers
cd canfar-containers
```

Fork workflow:

```bash
gh repo fork astroai/canfar-containers --clone
cd canfar-containers
git checkout -b my-change
```

## Prerequisites

- **Docker** with **buildx**
- Disk for multi-stage builds
- Harbor push is maintainer-only — local build/test needs no registry write access

## What to change where

| You want to… | Edit | Rebuild |
|--------------|------|---------|
| User-facing session guide | `docs/USAGE.md` | Yes — copied into `base` as `/opt/astroai/USAGE.md` |
| Contributor / dev workflow | `docs/CONTRIBUTING.md` | No |
| Portal registration, Harbor | `docs/OPERATORS.md` | No |
| Shell env, caches, `uv`/`pixi` paths | `scripts/astroai-profile.sh` | Yes — `base`+ |
| Session startup | `scripts/common-init.sh`, `scripts/startup-*.sh` | Yes |
| System packages | `dockerfiles/base/Dockerfile` | Yes — `base`+ |
| Python / uv / pixi foundation | `dockerfiles/python/Dockerfile` (untagged bake parent, not a Harbor image) | Full stack |
| Jupyter config | `config/jupyter_server_config.py` | `notebook` |
| Marimo starter notebook | **Edit in** [astroai-lab](https://github.com/astroai/canfar-lab) `data/notebooks/starter.py`, then `make sync-marimo-starter` | `marimo` |
| Jupyter / Ray starters | **Edit in** lab `data/notebooks/`, then `make sync-notebook-starters` | `notebook` |
| CADC client list | `config/cadc-tools.txt` | `base`+ |
| **`astroai` CLI** | `config/astroai-lab.in` + `config/astroai-lab.lock` | `base`+ |
| Ray | `config/ray-deps.txt`, `dockerfiles/ray-*`, `ray/`, `scripts/*ray*` | `make build-ray` |
| Bake graph, tags | `docker-bake.hcl`, `Makefile` | Depends |

Interactive `base` keeps compilers and session tools (scientists compile in
webterm/vscode). CUDA and heavy science stacks still belong in user pixi/uv
projects. Slim `ray-base` (workers only) stays minimal — see [RAY.md](RAY.md).

## Local build and test

```bash
make build/webterm
make build-all
./scripts/test-local.sh webterm 5000
./scripts/test-local.sh notebook 8888
```

After profile or base changes:

```bash
./scripts/test-local.sh webterm 5000
# inside container:
source /etc/profile.d/astroai.sh
astroai status
uv run python -c "print('ok')"
```

## Refresh the `astroai-lab` lock

`config/astroai-lab.in` tracks `astroai-lab` `main` unpinned. Images install from the compiled lock (git SHA). After lab lands on `origin/main`, CI `lock-check` fails until you regenerate:

```bash
cd ../astroai-lab
uv run pytest -q
cd ../canfar-containers
make lock-astroai-lab
(cd ray/manager && uv lock)
make lock-check
make build-all BUILD_TAG=local
make test-local BUILD_TAG=local
make test-ray BUILD_TAG=local
```

Same pattern for `make lock-ray` when unpinned Ray deps move. OpenResearch pins an upstream alphaXiv release (`ORX_VERSION` + `ORX_SHA256` in its Dockerfile); bump both together on new releases.

## Writable CADC venv

`/opt/astroai/venv/cadc` is writable so users can run `upgrade-cadc-tools.sh` or
`uv pip install --python /opt/astroai/venv/cadc …` for this session only.
Project deps use pixi/uv under `WORK`; caches prefer scratch via
`astroai`.

## Ray tests

```bash
make test-ray BUILD_TAG=local
make test-canfar-ray TAG=26.09
make test-canfar-ray-gpu TAG=26.09
```

| Script | Checks |
|--------|--------|
| `scripts/test-ray-ui-local.sh` | Manager HTML / JSON / redirects |
| `scripts/test-astroai-lab-loop.sh` | Cold start → save → resume in `base` |
| `scripts/test-canfar-ray.sh` | CANFAR manager UI + cluster lifecycle |

Integration tests for the CLI live in
[canfar-lab](https://github.com/astroai/canfar-lab)
(`tests/integration/test_cold_start_save_resume.py`).

## Marimo starter sync

Canonical `starter.py` lives in **astroai-lab**
(`src/astroai_lab/data/notebooks/starter.py`). The copy under
`config/notebooks/starter.py` is what the marimo image installs — keep them
identical:

```bash
# from canfar-containers (sibling checkout of canfar-lab / astroai-lab)
make sync-marimo-starter
```

Startup (`scripts/startup-marimo.sh`) seeds that file once into
`WORK/notebooks`, sources `~/.astroai/lab/.env` / `agent-env.sh`, runs
`astroai agent setup marimo` (OpenRouter into shared `.env` + `~/.marimo.toml`,
does not overwrite user settings), and opens `starter.py`. Keep
`canfar_marimo.VOSpaceUI` until vos fsspec lands. Project env activation for
cloned repos is `canfar_marimo.use_project` / `project_env_controls` in the
starter.

---

## Pull requests

```bash
git add -A
git commit -m "Short summary of why"
git push -u origin my-change
gh pr create --fill
```

Keep PRs focused. Do not commit Harbor credentials, `.env` secrets, personal API
keys, or large binary artifacts unrelated to image build context.

### Checklist

- [ ] `docs/USAGE.md` updated when user-visible behavior changes
- [ ] Upstream [astroai-lab](https://github.com/astroai/canfar-lab) updated when CLI or path behavior changes
- [ ] `dockerfiles/base/Dockerfile` still copies `docs/USAGE.md` correctly
- [ ] `./scripts/test-local.sh` run when scripts or Dockerfiles change
- [ ] Post-push release gate: `make test-canfar-agents TAG=…` (lightweight agent verb-surface probe on CANFAR; required after every image push — see OPERATORS.md)
- [ ] Image layers stay lean — prefer documenting heavy deps in USAGE.md

## Publishing

Image push and portal registration: [OPERATORS.md](OPERATORS.md).

## Questions

Open a [GitHub issue](https://github.com/astroai/canfar-containers/issues) or
comment on a PR with `gh pr comment`.
