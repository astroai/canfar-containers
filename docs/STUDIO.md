# AstroAI Studio

Contributed session image `images.canfar.net/astroai/studio:<tag>` — browser
coding portal powered by DeepSeek Harness with AstroAI's owned `astroai`
profile (not stock `dsh web`).

## Architecture

| Process | Bind | Role |
|---------|------|------|
| `astroai studio --prepare --profile canfar` | (boot, sync) | Writes `$DSH_HOME/profiles/astroai` before dsh starts |
| `dsh --profile astroai` | `127.0.0.1:3080` | Coding agent SPA (upstream refuses `0.0.0.0`) |
| `agent-wizard.py` | `127.0.0.1:4792` | AstroAI hub (`/astroai-agents/`) — batch compute + agents |
| `ghostty-web` | `127.0.0.1:4793` | Browser terminal (`/astroai-terminal/`) |
| `studio-canfar-proxy.py` | `0.0.0.0:5000` | Public edge; assets rewrite + `/api` shim + chips + WS splice |

Boot fails loud if `--prepare` fails (stale `astroai-lab.lock` or missing CLI).

## Proxy / API / WebSocket (required for Skaha)

dsh builds every RPC as `new URL('/api/…', location.origin)` and validates the
channel with `CHANNEL_PATTERN` (single path segment). Rewriting the string
`"/api"` in JS bundles to `/session/contrib/<id>/api` **breaks** the client.

Instead the proxy:

1. Leaves the channel string `"/api"` alone (`CHANNEL_PATTERN`).
2. Rewrites quoted `"/api/…"`, `/assets`, favicon, hub, terminal, and `/plugins/`
   paths in HTML/JS/CSS (covers `/api/file` img URLs, present.open, remote.mux).
3. Injects an early **fetch + WebSocket + EventSource shim** that prefixes
   same-origin `/api…` and `/plugins…` URLs with `/session/contrib/<id>`.
   The shim also sets `__DSH_TRANSPORT__.ownsHost = true` so Settings →
   Models / providers persist: dsh otherwise treats non-loopback page hosts
   (`workloads.canfar.net`) as memory-only (“settings are unavailable in
   this browser”).
4. Injects **Terminal** + **AstroAI** chips (same overlay as openresearch) and
   brands the tab as AstroAI Studio (session hostname when set).
5. **Splices WebSocket upgrades** (dsh `/api/remote.mux` and ghostty `/ws`).
6. Forwards browser **Host** and **Origin** to dsh. Start dsh with
   `--trusted-host <public-host>` (startup trusts `ws-uv.canfar.net`,
   `ws-uvic.canfar.net`, `staging.canfar.net`, `workloads.canfar.net`,
   `workload-uv.canfar.net`, plus `ASTROAI_STUDIO_TRUSTED_HOST` and the
   pod hostname). Forces `Accept-Encoding: identity` upstream so HTML/JS
   rewrites see plaintext (browser `gzip` otherwise skips all rewrites).
7. **Skaha Connect token redirect:** Skaha's Connect URL is
   `/session/contrib/<id>/` without dsh's one-shot `?token=`. Startup
   scrapes the token from dsh's boot log into `$ASTROAI_STUDIO_STATE/dsh-web-token`;
   the proxy 302s bare index hits (no auth cookie) to `/?token=…` under the
   session prefix so the SPA can set `dsh-auth-*` and stop 401ing.
8. Rewrites dsh `303 Location: /` and `<base href="/">` to the session
   prefix — otherwise the browser leaves `/session/contrib/<id>/` or
   resolves `./assets` at the workloads site root (blank page).

Do **not** put Studio behind vscode `/proxy/3080/` — absolute `/api` still
escapes that path (canfar-lab review-bench HOWTO §11).

SSE (`Accept: text/event-stream`, `/api/events`) is streamed without body rewrite.

Pinned dsh: `@deepseek-ai/dsh@0.1.5-rc.2` (matches canfar-lab `dsh.yaml` /
`studio.DSH_VERSION`).

## Image bake notes

- Bake `orx-wire-compute.py` for OpenResearch hub flows when that image needs it.
- Studio embeds the AstroAI hub + ghostty-web (same chips as openresearch).
- Refresh `config/astroai-lab.lock` after canfar-lab ships Studio prepare/doctor
  fixes; a stale lock silently lacks `--prepare` / Team preset / MCP tools.
- Include `studio` in Harbor cleanup and CANFAR session smokes.

## Laptop

```bash
astroai studio                 # cwd
astroai studio /path/to/repo   # explicit
astroai studio --profile laptop
astroai studio --doctor        # pre-flight (exit 1 on fatal)
```

## CANFAR

```bash
canfar create --name studio contributed images.canfar.net/astroai/studio:26.09
```

Connect URL → dsh coding UI (`astroai` profile). Top-right chips: **Terminal**
(ghostty-web) and **AstroAI** (hub). For a dedicated shell-only session, launch
`images.canfar.net/astroai/terminal:<tag>` instead.

From the **AstroAI** hub:

- **Start batch compute** — launches an autoscaling ray-manager (workers 0–8).
- **Install** — agent CLIs land on `$SCRATCH/.local/bin` (not `/arc/home`).
- **Setup** — agent configs, MCP, and rules on `$HOME` (`/arc/home`).

dsh workspaces are durable under `$HOME/.dsh` (on `/arc`). Studio boots with
cwd / dsh `defaultCwd` = `$SRCDIR` (usually `/scratch/src`; override with
`ASTROAI_STUDIO_CWD`). A name like `torchregress` in the sidebar is a prior
saved workspace — pick or create one under `$SRCDIR` via the directory picker.

In-session health:

```bash
astroai studio --doctor --profile canfar
ps aux | grep 'dsh --profile astroai'
```

## Skills

```bash
npx skills add astroai/canfar-skills
```

Studio also discovers `~/.astroai/lab/review-bench/skills` (includes
`review-panel` and `canfar-session`).
