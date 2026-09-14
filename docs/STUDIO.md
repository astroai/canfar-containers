# AstroAI Studio

Contributed session image `images.canfar.net/astroai/studio:<tag>` — browser
coding portal powered by upstream DeepSeek Harness (`dsh web`).

## Architecture

| Process | Bind | Role |
|---------|------|------|
| `dsh web` | `127.0.0.1:3080` | Coding agent SPA (upstream refuses `0.0.0.0`) |
| `agent-wizard.py` | `127.0.0.1:4792` | AstroAI hub (agents + Start batch compute) |
| `studio-canfar-proxy.py` | `0.0.0.0:5000` | Public edge; assets rewrite + `/api` shim + WS splice |

## Proxy / API / WebSocket (required for Skaha)

dsh builds every RPC as `new URL('/api/…', location.origin)` and validates the
channel with `CHANNEL_PATTERN` (single path segment). Rewriting the string
`"/api"` in JS bundles to `/session/contrib/<id>/api` **breaks** the client.

Instead the proxy:

1. Leaves the channel string `"/api"` alone (`CHANNEL_PATTERN`).
2. Rewrites quoted `"/api/…"`, `/assets`, favicon, hub, and `/plugins/` paths
   in HTML/JS/CSS (covers `/api/file` img URLs, present.open, remote.mux).
3. Injects an early **fetch + WebSocket + EventSource shim** that prefixes
   same-origin `/api…` and `/plugins…` URLs with `/session/contrib/<id>`.
4. **Splices WebSocket upgrades** (dsh `/api/remote.mux`) like the marimo
   HTML proxy — plain `HTTPConnection` cannot upgrade.
5. Forwards browser **Host** and **Origin** to dsh. Start dsh with
   `--trusted-host <public-host>` (startup trusts `ws-uv.canfar.net`,
   `ws-uvic.canfar.net`, `staging.canfar.net`, plus
   `ASTROAI_STUDIO_TRUSTED_HOST` and the pod hostname).

Do **not** put Studio behind vscode `/proxy/3080/` — absolute `/api` still
escapes that path (canfar-lab review-bench HOWTO §11).

SSE (`Accept: text/event-stream`, `/api/events`) is streamed without body rewrite.

Pinned dsh: `@deepseek-ai/dsh@0.1.5-rc.2` (matches canfar-lab `dsh.yaml`).

## Image bake notes

- Bake `orx-wire-compute.py` next to `agent-wizard.py` (hub → Start batch compute).
- Refresh `config/astroai-lab.lock` after canfar-lab ships `astroai studio`;
  a stale lock silently lacks `--prepare` / Team preset.

## Laptop

```bash
astroai studio                 # cwd
astroai studio /path/to/repo   # explicit
astroai studio --profile laptop
```

## CANFAR

```bash
canfar create --name studio contributed images.canfar.net/astroai/studio:26.09
```

Connect URL → dsh coding UI. Blue **AstroAI** chip → `/astroai-agents/` hub.
Team review: pick the **AstroAI Studio Team** preset in New session (or
`astroai panel run` headless).

## Skills

```bash
npx skills add astroai/canfar-skills
```

Studio also discovers `~/.astroai/lab/review-bench/skills` (includes
`review-panel` and `canfar-session`).
