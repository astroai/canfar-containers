# Distributed Ray on AstroAI (CANFAR)

User-owned Ray clusters: a **contributed `ray-manager`** session launches
**headless `ray-worker`** sessions over pod networking. Images are published as
`images.canfar.net/astroai/ray-manager:<tag>` and
`images.canfar.net/astroai/ray-worker:<tag>`.

```mermaid
flowchart TB
  User[You] --> Portal["Science Portal / canfar create"]
  Portal --> Mgr["ray-manager :5000"]
  Mgr --> Pref[Network preflight]
  Pref --> W1[ray-worker]
  Pref --> W2[ray-worker]
  Mgr --> Dash["/dashboard/ → Ray Dashboard :8265"]
  Mgr --> Jobs["ASTROAI_RAY_JOBS_ADDRESS → Jobs API"]
  Jobs --> Run["astroai run"]
```

## Prefer

| Path | Why |
|------|-----|
| AstroAI hub → **Start batch compute** | Autoscaling manager + OpenResearch wire |
| **`astroai cluster start`** | Same from a terminal (always autoscaling) |
| **`astroai run train.py`** | Runs a program on that cluster and waits |
| Ray Dashboard at `connectURL/dashboard/` | Watch jobs, nodes, logs. Not the submit command. |
| Manager control panel at `/` | Auth, network check, fallback create/stop |
| One `ray-worker` image | Request `gpus=N` per worker; CPU and GPU share the image |


The FastAPI control panel is feature-frozen for stability (`ray/manager/FROZEN.md`).
ML/CUDA stacks live in user pixi/uv projects. Spill/temp need **`/scratch`** on
every node. Persist cluster state under `/arc/home/<user>/` or
`/arc/projects/<group>/` — not the `/arc` mount root.

## Images

| Image | Skaha type | Portal | Parent |
|-------|------------|--------|--------|
| `ray-manager` | Contributed | Register — users launch this | Fat `base` (compilers + shell tools) |
| `ray-worker` | Headless | Manager launches workers | Slim `ray-base` (python bake stage) |
| `ray-base` | Build-only | — | Minimal apt + `astroai` + Ray |

Workers join with the image Ray venv. Env snapshots stay on `/arc`
(`astroai save` / `resume` in an interactive session). `/scratch` is
**per-pod** — not shared with the manager or other sessions; put shared data
on `/arc`.

## Build and test

```bash
make build-ray BUILD_TAG=26.09
make test-ray
make push-ray TAG=26.09
make test-canfar-ray TAG=26.09
make test-canfar-ray-gpu TAG=26.09
```

Ray layers use the **same bake `TAG` as `base`**.

For Jobs / Dashboard on CANFAR, start the manager with **≥8 GiB** memory.

If headless probes hang Pending, see
[OPERATORS.md — platform notes](OPERATORS.md#platform-notes-headless-pending)
or set `CANFAR_RAY_SKIP_PREFLIGHT=1` for UI-only checks.

## Authentication

From any AstroAI session (webterm/vscode):

```bash
canfar login
canfar create --name raymgr contributed images.canfar.net/astroai/ray-manager:26.09
```

Credentials persist as `~/.canfar/config.yaml` (and optionally
`~/.ssl/cadcproxy.pem`) on `/arc/home`. The manager reuses that volume to launch
workers via the `canfar` Python client.

For maintainer headless pulls when required:

```bash
canfar config set registry.url https://images.canfar.net
canfar config set registry.username <harbor-user>
canfar config set registry.secret <harbor-cli-secret>
```

## Network preflight

Preflight starts a headless probe and checks **worker→manager** TCP on Ray ports
(6379–6381). Manager→worker samples against the probe pod are not used (the probe
never listens on Ray ports).

| Outcome | Meaning |
|---------|---------|
| Probe stays **Pending** | Headless scheduling issue — [science-platform#1124](https://github.com/opencadc/science-platform/issues/1124) |
| `worker→manager` checks fail | Often **wrong Skaha server** in `~/.canfar` on `/arc/home/<user>` (e.g. manager on staging, `active.server=canfar` → workers on production). Also possible: true session-to-session network isolation |
| Worker log: cannot reach head `:6379` | Same class — confirm worker and manager are on the same server (`canfar auth show` / session lists) before assuming platform isolation |

**Server pin:** `/arc/home/<user>/.canfar` must use the same `active.server` as the cluster where the manager runs. Registry bootstrap can set `ACTIVE_SERVER=staging` (or `canfar`). Manager sessions also accept `CANFAR_ACTIVE_SERVER` / `ACTIVE_SERVER` env to re-pin on startup.

Preflight results are bound to the manager pod IP. Creating a cluster after moving
to a new manager session requires a fresh preflight.

## Web UI

Contributed **`ray-manager`** serves port **5000** under
`/session/contrib/<session-id>/` (prefix stripped before the container).

| Surface | Purpose |
|---------|---------|
| `/` | Auth, preflight, create/stop cluster, worker table |
| `/dashboard/` | Official Ray Dashboard (proxy to `127.0.0.1:8265`) |
| `/actions/*` | Form POSTs for cluster lifecycle |
| `/api/v1/*` | JSON automation |

Always open the Dashboard **with a trailing slash**, using the session connect
URL (`…/dashboard/`), not a bare workloads hostname.

On the manager pod, Jobs clients use **`ASTROAI_RAY_JOBS_ADDRESS`**
(`http://127.0.0.1:8265`).

### OpenResearch (`orx`) on Ray

AstroAI’s `openresearch` image defaults compute to CANFAR batch (Ray Jobs under
the hood). Preferred path:

```bash
# From openresearch (or any AstroAI session with canfar auth):
# AstroAI hub → Start batch compute
# Then in OpenResearch: run experiments (no --backend needed)
```

Manual:

```bash
export ASTROAI_RAY_JOBS_ADDRESS=http://127.0.0.1:8265   # on the manager
# or connectURL/dashboard from another session
orx exp run <expId> --backend ray
```

CANFAR session create/join stays in the AstroAI hub (`/astroai-agents/`) or
ray-manager — not in upstream OpenResearch’s Compute list.

Local UI smoke: `./scripts/test-ray-ui-local.sh` (part of `make test-ray`).

## Cluster workflow

Usual path: autoscaling. One click or one command, then a job with `--cpus`.

```bash
# AstroAI hub → Start batch compute
# or:
astroai cluster start
export ASTROAI_RAY_JOBS_ADDRESS=…    # printed by start
astroai run train.py --cpus 2
```

`cluster start` writes `~/.config/canfar/lab/ray-manager.env` and creates the
manager if needed. Ray adds `ray-as-*` workers when the job needs CPUs.
Size the ceiling with `--min-workers` / `--max-workers` / `--cores` / `--ram`
/ `--gpus`. If a manager was already running when you changed sizing, stop it
and start a new one so it sources the env file.

The manager UI still exists for auth, the network check, and fallback
create/stop. Sequence when using the UI:

```mermaid
sequenceDiagram
  participant U as User
  participant M as ray-manager
  participant C as canfar / Skaha
  participant W as ray-workers
  U->>M: Run network preflight
  M->>C: Headless probe
  U->>M: Create cluster N workers
  M->>C: Launch ray-worker sessions
  C->>W: Start workers
  W->>M: Join Ray head
  U->>M: Open /dashboard/ or run jobs from CLI
  U->>M: Stop cluster
  M->>C: Delete workers
```

1. **Run network preflight**
2. **Create cluster** — worker count, CPU/RAM, GPUs per worker, `min_joined`, partial-start policy
3. **Use Ray** — Dashboard, `ray.init(address="auto")`, or `astroai run train.py --cpus 2 --memory 8GiB`
4. **Stop cluster** — destroys worker sessions

Partial-start policies: `accept_partial`, `fail_and_cleanup`, `continue_waiting`.

State lives at `~/.astroai/ray/clusters/<cluster-id>/state.json` (worker logs archived
beside it). Each manager session defaults `RAY_CLUSTER_ID` to `mgr-<skaha_sessionid>`
so a new manager does not inherit another pod’s `default` state on shared `/arc/home`.
Override `RAY_CLUSTER_ID` for a stable team path under `/arc/projects` if needed.
On manager start, terminal-phase leftovers are destroyed (startup GC); **Reconcile
state** refreshes membership for an active cluster after restart.

## Autoscaling (Ray-native, on demand)

Ray's own autoscaler launches and destroys `ray-worker` sessions as jobs need
CPUs. The manager head starts with
`ray start --head --autoscaling-config=<yaml>` when enabled; a CANFAR
`NodeProvider` (in `astroai_workload`, `ray-as-*` sessions) does the scaling.

Turn it on with the hub button or:

```bash
astroai cluster start
```

That writes `~/.config/canfar/lab/ray-manager.env` (Skaha rejects `-e` on
contributed sessions). `startup-ray-manager.sh` sources the file before
launching the head. Head advertises 0 CPUs, so a job with `--cpus ≥ 1`
triggers workers. Idle workers stop after
`RAY_AUTOSCALING_IDLE_TIMEOUT_MINUTES` (default 5). Delete the file to turn
autoscaling off for the next manager.

Verify end-to-end on CANFAR (manager UI + dynamic scale-up + idle scale-down):

```bash
make test-canfar-ray-autoscale TAG=26.09
```

## Manager API

| Endpoint | Purpose |
|----------|---------|
| `GET /api/v1/auth/status` | Credential check |
| `POST /api/v1/preflight/run` | Network preflight (`?async=1`) |
| `POST /api/v1/cluster/create` | Launch workers (`?async=1`) |
| `POST /api/v1/cluster/stop` | Stop and destroy workers |
| `POST /api/v1/cluster/reconcile` | Refresh state |
| `POST /api/v1/cluster/clean-orphans` | Destroy untracked workers |
| `POST /api/v1/workers/{id}/retry` | Retry a failed worker |
| `GET /api/v1/status` | Full cluster JSON |
| `GET /api/v1/workers/{id}/logs` | Archived worker logs |

## Layout

```
ray/manager/                 FastAPI + cluster lifecycle
ray/worker/                  Worker entrypoint helpers
scripts/test-ray-*.sh        Local and CANFAR tests
examples/ray/                Container smokes
```

## Related

- [USAGE.md](USAGE.md) — general sessions
- [OPERATORS.md](OPERATORS.md) — publish and platform notes
- [astroai-lab](https://github.com/astroai/canfar-lab) — `astroai cluster start` + `run` (not `ray job submit`)
- Starter notebook in-image: `/opt/astroai/notebooks/ray_train.ipynb`
