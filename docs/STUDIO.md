# AstroAI Studio

Contributed session image `images.canfar.net/astroai/studio:<tag>` — unified 5-in-1 development studio (AI Coding Agents, Web Terminal, JupyterLab 4, Marimo, VS Code Web) for CANFAR.

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

## Glassmorphism Command Dock

All web interfaces in Studio feature an injected, floating Glassmorphism Command Dock:
- **One-click tool switching** between Agents, Terminal, JupyterLab, Marimo, and VS Code.
- **Middle-click or Cmd/Ctrl-click** opens any tool in a new browser tab.
- **Status Indicator**: Live pulsing dot reflects health of the underlying daemons.
- **Toggle visibility**: `Cmd+K` / `Ctrl+K` or clicking the close toggle.

## Storage Isolation & Zero-Cache Hygiene

Studio strictly isolates runtime state on `/scratch`:
- `${SCRATCH}/.studio-${USER}/logs/` — daemon logs (`dsh.log`, `ghostty.log`, `jupyter.log`, `marimo.log`, `vscode.log`, `wizard.log`).
- `${SCRATCH}/.studio-${USER}/pnpm-store` / `pnpm-home` — isolated Node/pnpm package stores.
- `${SCRATCH}/.studio-${USER}/jupyter-runtime` / `jupyter-data` — ephemeral Jupyter state.
- `${SCRATCH}/.studio-${USER}/vscode-data` / `vscode-extensions` — VS Code runtime data.
- Durable profiles and credentials remain safe on `/arc/home/${USER}` (`~/.dsh/`, `~/.canfar/`).

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
