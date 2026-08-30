# AGENTS.md

Operating guidance for coding agents and maintainers working in `astroai/canfar-containers`.

## Mission

`canfar-containers` builds and maintains session and platform container images for astronomy and machine learning on the [CANFAR Science Platform](https://www.opencadc.org/canfar/). Images are published to Harbor under `images.canfar.net/astroai/<image>:<tag>`.

## Stack and Architecture

- **Bake Graph (`docker-bake.hcl`):**
  - Untagged `python` stage (Python 3.13 + pixi foundation; uv remains the image-layer pip installer).
  - Fat `base` (compilers + session tools) → interactive sessions (`webterm`, `ghostty-web`, `vscode`, `notebook`, `marimo`, `openresearch`) and `improc` stack (`improc`, `improc-webterm`, `improc-notebook`).
  - Slim `ray-base` → `ray-worker`; fat `base` → `ray-manager`.
- **Ray Stack (`ray/`):**
  - FastAPI-based Ray cluster manager app (`ray/manager/`) and worker lifecycle helpers (`ray/worker/`).
- **In-Session Integrations:**
  - `astroai` CLI and lab utilities installed via lockfiles (`config/astroai-lab.lock`).
  - Writable CADC environment (`/opt/astroai/venv/cadc`).

## Verification Commands and Tiers

### 1. Fast Static & Host Tests (Docker-Free — Pre-Push Gate)

Run these before any commit or push. They require no Docker daemon:

```bash
make lint
```

This runs:
- `make lock-check`: Fails if lockfiles drift from their source requirements (`config/astroai-lab.in`, `config/ray-deps.txt`).
- `make lint-doc-quota`: Verifies doc assertions.
- `make test-host`: Runs Docker-free host self-checks (peek/osc52, agent-wizard unit tests, Starship prompt configuration).

Ray manager unit tests:
```bash
(cd ray/manager && pixi run lint && pixi run test)
```

### 2. Lockfile Maintenance

When upstream `astroai-lab` or Ray dependencies are updated:

```bash
make lock-astroai-lab     # regenerate config/astroai-lab.lock
make lock-ray             # regenerate config/ray-deps.lock
(cd ray/manager && pixi lock)
make lock-check           # confirm lockfile integrity
```

### 3. Docker Local Smokes

Requires Docker with buildx:

```bash
make build-all BUILD_TAG=local
make test-local           # local smoke test on all session images
make test-improc-local    # verify astronomy improc CLIs
make test-ray SMOKE=1     # fast Ray container & UI smoke
```

## Agent PR Hygiene and Rules

1. **Agent-Neutral Instructions:** All agents (Antigravity, Cursor, Codex, Claude, OpenCode, Pi) use this `AGENTS.md` as the single canonical repository guide. Do not create agent-specific instruction duplicates.
2. **Lockfile Integrity:** Never weaken or bypass `make lock-check` in CI or local scripts. Maintain strict reproducible dependency discipline.
3. **No Secrets / Local Tags:** Never commit API keys, `.env` files, or production Harbor credentials.
4. **Minimal Diffs:** Batch mechanical refactors and avoid noisy whitespace or formatting-only changes across unrelated files.
5. **Follow `~/src/AGENTS.md`:** Ship changes via GitHub pull requests against `astroai/canfar-containers`.
