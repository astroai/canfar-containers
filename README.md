# AstroAI containers

Session images for astronomy and ML on the
[CANFAR Science Platform](https://www.opencadc.org/canfar/).
Images publish to Harbor as `images.canfar.net/astroai/<image>:<tag>`.

Licensed under [BSD-2-Clause](LICENSE).

```mermaid
flowchart TB
  subgraph astroai [AstroAI]
    Imgs["Harbor: images.canfar.net/astroai/*"]
    Lab["astroai workbench CLI"]
  end
  subgraph canfar [CANFAR]
    Portal[Science Portal]
    Skaha[Skaha / sessions /arc /scratch]
    CLI["canfar CLI"]
  end
  Portal --> Imgs
  CLI --> Skaha
  Imgs --> Skaha
  Lab --> Skaha
```

## Names at a glance

| Name | Meaning |
|------|---------|
| **AstroAI** | This product: GitHub [`astroai`](https://github.com/astroai), Harbor project `astroai`, images and tools |
| **CANFAR** | Hosting platform: portal, Skaha, auth, `/arc`, scheduling |
| **`canfar`** | Platform CLI — login, create/list/delete sessions |
| **`astroai`** | In-session CLI: project env, Ray cluster/jobs, agents |
| **`images.canfar.net/astroai/*`** | AstroAI images on CANFAR Harbor (host ≠ product name) |

## Sessions

| Image | Use for | Skaha type |
|-------|---------|------------|
| `webterm` | Browser terminal (ttyd + tmux) | Contributed |
| `ghostty-web` | Browser terminal (ghostty-web + tmux) | Contributed |
| `vscode` | Browser IDE (OpenVSCode Server) | Contributed |
| `notebook` | JupyterLab | Notebook |
| `marimo` | Reactive notebooks | Contributed |
| `openresearch` | OpenResearch (`orx`) autoresearch dashboard | Contributed |
| `base` | Headless parent (CI / batch) | — |
| `improc` | Astronomy FITS/HDF5 image-processing CLIs | Headless |
| `improc-webterm` | Same tools + browser terminal (ttyd/tmux) | Contributed |
| `improc-notebook` | Same tools + JupyterLab (improc kernel) | Notebook |
| `ray-manager` | Ray head + control panel + Dashboard ([RAY.md](docs/RAY.md)) | Contributed |
| `ray-worker` | Ray worker CPU or GPU (manager-launched) | Headless |

## Documentation

| Doc | Audience |
|-----|----------|
| [docs/USAGE.md](docs/USAGE.md) | Session users — first session, storage, tools |
| [astroai USAGE](https://github.com/astroai/canfar-lab/blob/main/docs/USAGE.md) | `astroai` CLI detail |
| [docs/RAY.md](docs/RAY.md) | Ray clusters — manager + workers |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | Developers — clone, build, test, PRs |
| [docs/OPERATORS.md](docs/OPERATORS.md) | Maintainers — push, register, smoke tests |

In-session: `astroai help` · `less /opt/astroai/USAGE.md`

## Build and test

Requires Docker with buildx. Full loop: [CONTRIBUTING.md](docs/CONTRIBUTING.md).

```bash
make build-all              # session stack
make build-ray              # ray-manager + ray-worker
make build-improc           # astronomy image-processing CLIs (+ base)
make build/vscode           # one image (+ parents)
make test-local             # local smokes
make test-improc-local      # improc CLI smoke
make test-ray               # local Ray cluster + UI
```

## Push (maintainers)

See [OPERATORS.md](docs/OPERATORS.md). The `astroai` Harbor project is **public**
(anonymous pull); push still needs `docker login images.canfar.net`.

```bash
make push/vscode TAG=26.08
make push-all TAG=26.08
make push-ray TAG=26.08
make push-improc TAG=26.08
```

Default `TAG` is current UTC `YY.MM` (for example `26.08`).

## Layout

```
dockerfiles/   python (untagged bake parent) → base → sessions | improc; python → ray-base → worker; base → ray-manager
ray/           manager FastAPI app + worker helpers
scripts/       startup-*.sh, test-*.sh, profile
config/        astroai-lab.lock, ray-deps.lock, improc-py.txt, notebooks (synced from lab)
docs/          USAGE, RAY, OPERATORS, CONTRIBUTING
examples/ray/  container-local Ray smokes
```

## Design

- **Same images for CPU and GPU** — choose the node in the portal; CUDA/ML stacks via pixi/uv in the project.
- **Bake graph:** untagged `python` stage → fat `base` (compilers + session tools) → interactive sessions and `improc`; slim `ray-base` (from that python stage) → `ray-worker`; fat `base` → `ray-manager`. `python` is not published to Harbor.
- **Fast session disks:** `WORK` is `$SCRATCH/src` when `/srcdir` is the container overlay (OOM-fragile) and `/scratch` is a volume; `SCRATCH` (`/scratch`) holds data and caches. Both are session-private. `/arc/home` and `/arc/projects` are shared across sessions. Persist with `astroai save` / `git push`.
- **Skaha types:** Contributed listen on **5000**; Notebook on **8888**.
- **Auth at the edge:** Session UIs trust CANFAR TLS + portal login. Use these images only behind an authenticating reverse proxy.

Heavy site software: [CVMFS on CANFAR](https://github.com/opencadc/canfar/blob/main/docs/platform/cvmfs.md).

## Related repos

| Repo | Role |
|------|------|
| [astroai-lab](https://github.com/astroai/canfar-lab) | In-session CLI (`astroai`) |
| [opencadc/canfar](https://github.com/opencadc/canfar) | Platform client |
| [opencadc/science-platform](https://github.com/opencadc/science-platform) | Skaha / Helm (platform team) |

## Contributing

Pull requests welcome — [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).
