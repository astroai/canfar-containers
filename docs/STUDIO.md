# AstroAI Studio

Contributed session image `images.canfar.net/astroai/studio:<tag>` — unified 5-in-1 development studio (AI Coding Agents, Web Terminal, JupyterLab 4, Marimo, VS Code Web) for CANFAR.

`improc-studio` and `specproc-studio` are the same image with `improc` or `specproc` as the parent. On those images the science venv is the default Jupyter kernel, and marimo runs inside that venv.

## Architecture

| Process | Bind | Role | Public Path |
|---------|------|------|-------------|
| `studio-canfar-proxy.py` | `0.0.0.0:5000` | Ingress multiplexer, WS splicer, Command Dock | `/` |
| `dsh --profile astroai` | `127.0.0.1:3080` | Coding agent SPA (DeepSeek Harness) | `/` |
| `ghostty-web` | `127.0.0.1:4793` | Browser terminal with tmux and bash | `/terminal/` |
| `jupyter lab` | `127.0.0.1:8888` | JupyterLab 4 interactive notebooks | `/jupyter/` |
| `marimo edit` | `127.0.0.1:2718` | Marimo reactive scientific notebooks | `/marimo/` |
| `openvscode-server` | `127.0.0.1:8080` | OpenVSCode Server web IDE | `/vscode/` |
| `agent-wizard.py` | `127.0.0.1:4792` | Compute hub (Ray clusters + batch jobs) | `/hub/` |
| `canfar-lab sync watch` | poll | Desk mailbox doorbell: absorb desk requests, reply with events | — |

## Glassmorphism Command Dock

All web interfaces in Studio feature an injected, floating Glassmorphism Command Dock:
- **One-click tool switching** between Agents, Terminal, JupyterLab, Marimo, and VS Code.
- **Middle-click or Cmd/Ctrl-click** opens any tool in a new browser tab.
- **Status Indicator**: Live pulsing dot reflects health of the underlying daemons.
- **Toggle visibility**: `Cmd+K` / `Ctrl+K` or clicking the close toggle.

## Desk Sync

The session runs `canfar-lab sync watch --session --interval 30` (started by
`startup-studio.sh`, restarted by the supervision loop if it dies). It polls the
shared mailbox at `~/.astroai/lab/sync` — a doorbell only; GitHub is the
protocol — and answers desk requests by bootstrapping missing repos and
fast-forwarding. Divergent or dirty trees stop with a report; nothing is ever
stashed.

The dsh page also carries the **Desk dock** card (vendored from canfar-lab as
`scripts/lib/astroai-dock.html`, drift-gated by `make desk-assets-check`):

- **Desktop sync** — commit + push this session's repos (secret-scan gated) and
  drop the desk an event to pull.
- **Launch cluster** — start the CANFAR Ray cluster (never a local one).
- Chips show session status, the image dsh and canfar-lab versions, and the
  last exchange time.

`GET /api/studio/sync` serves the dock state; `POST /api/studio/sync/<action>`
(`sync_to_desktop`, `cluster`) runs it. Both require the per-process bearer
embedded in the injected page (or a loopback caller), and the exchange is
single-flight. Protocol details: canfar-lab `docs/desk-sync.md`.

## Storage Isolation & Zero-Cache Hygiene

Studio strictly isolates runtime state on `/scratch`:
- `${SCRATCH}/.studio-${USER}/logs/` — daemon logs (`dsh.log`, `ghostty.log`, `jupyter.log`, `marimo.log`, `vscode.log`, `wizard.log`).
- `${SCRATCH}/.studio-${USER}/pnpm-store` / `pnpm-home` — isolated Node/pnpm package stores.
- `${SCRATCH}/.studio-${USER}/jupyter-runtime` / `jupyter-data` — ephemeral Jupyter state.
- `${SCRATCH}/.studio-${USER}/vscode-data` / `vscode-extensions` — VS Code runtime data.
- Durable profiles and credentials remain safe on `/arc/home/${USER}` (`~/.dsh/`, `~/.canfar/`).

## Community Plugins

Studio ships the dsh plugin market ([dshmarket](https://github.com/dsh-market/dsh-market), baked under `/opt/astroai/dsh-plugins`).

- **Install:** Settings → Plugin Market → Discover → Install. Most plugins load live; the rest apply at the next session start.
- **Switch off/on:** Settings → Plugin Market → Installed. Switches are kept when Studio regenerates the profile at startup.
- **Restart:** the market's Restart button is disabled on CANFAR, because the session supervisor owns dsh. Start a new session to apply a change that needs a restart.
- **Compatibility:** the market refuses plugins that declare a newer dsh than the CLI baked in the image. The image installs `@deepseek-ai/dsh@${DSH_VERSION}` (the same pin as canfar-lab) and the current dshmarket, and allows that CLI as a peer.
- Installed plugins live in `~/.dsh/profiles/astroai` (durable). The image pins pnpm in a shared corepack home, so installs do not download pnpm into `$HOME`.

## CLI Usage

### From Laptop / Workstation

```bash
canfar create --name my-studio contributed images.canfar.net/astroai/studio:latest
canfar session connect <id>     # opens port 5000 in browser
```

Or run locally:
```bash
canfar lab studio               # cwd
canfar lab studio /path/to/repo # explicit path
canfar lab studio --doctor      # pre-flight verification
```

### Inside Session

```bash
canfar lab studio status        # inspect status of all 5 studio services
canfar lab open jupyter [path]  # obtain/open direct browser link to notebook
canfar lab open vscode [path]   # obtain/open direct browser link to file
canfar lab open marimo [path]   # obtain/open direct browser link to reactive notebook
```

## Building on Cloud Linux VM (`seb`)

```bash
ssh seb
cd ~/src/astroai/canfar-containers
git pull
docker buildx bake studio
```
