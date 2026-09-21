# syntax=docker/dockerfile:1
# AstroAI Studio: dsh coding portal (owned `astroai` profile) for CANFAR.
# Proxies :3080 → :5000 with path rewrite (see docs/STUDIO.md).
# Sidecars: AstroAI hub (:4792) + ghostty-web (:4793) under /astroai-*.

ARG REGISTRY=images.canfar.net
ARG OWNER=astroai
ARG BASE_NAME=base
ARG TAG=local
# Keep in sync with canfar-lab data/agent/agents/dsh.yaml
ARG DSH_VERSION=0.1.5-rc.2
# Keep in sync with dockerfiles/terminal/Dockerfile + openresearch.
ARG GHOSTTY_WEB_VERSION=0.4.0-next.20.g1858a59
ARG NODE_PTY_VERSION=1.1.0
ARG WS_VERSION=8.21.3

FROM ${REGISTRY}/${OWNER}/${BASE_NAME}:${TAG}

ARG DSH_VERSION
ARG GHOSTTY_WEB_VERSION
ARG NODE_PTY_VERSION
ARG WS_VERSION

LABEL io.canfar.skaha.session.type="contributed" \
      org.opencontainers.image.title="AstroAI Studio" \
      org.opencontainers.image.description="dsh-based coding portal for CANFAR" \
      org.opencontainers.image.source="https://github.com/deepseek-ai/deepseek-harness" \
      org.opencontainers.image.version="${DSH_VERSION}"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
# Global npm install into /opt/astroai so the binary is image-baked (not $HOME).
# corepack pnpm: needed for `dsh plugin` / Agent Teams when a session runs
# `astroai studio --prepare` (boot itself uses --no-install for a fast Connect URL).
RUN npm install -g --prefix /opt/astroai "@deepseek-ai/dsh@${DSH_VERSION}" \
    && /opt/astroai/bin/dsh --version \
    && corepack enable \
    && corepack prepare pnpm@9.15.9 --activate \
    && pnpm --version

# ghostty-web sidecar (same stack as astroai/terminal; proxy mounts /astroai-terminal/).
RUN apt-get update && apt-get install -y --no-install-recommends tmux \
    && rm -rf /var/lib/apt/lists/* \
    && printf '%s\n' \
        'set -g default-shell /bin/bash' \
        'set -g mouse on' \
        'set -g history-limit 50000' \
        'set -g status-position top' \
        'set -g status-left "#[fg=colour117,bold]AstroAI Studio "' \
        'set -g status-right "#[fg=colour245]#{host_short}"' \
        'set -s escape-time 10' \
        > /etc/tmux.conf

COPY scripts/ghostty-web-server.mjs /opt/ghostty-web/server.mjs
COPY scripts/ghostty-web-client.mjs /opt/ghostty-web/client.mjs
RUN chmod a+r /opt/ghostty-web/server.mjs /opt/ghostty-web/client.mjs \
    && npm install --omit=dev --prefix /opt/ghostty-web \
        "ghostty-web@${GHOSTTY_WEB_VERSION}" \
        "@lydell/node-pty@${NODE_PTY_VERSION}" \
        "ws@${WS_VERSION}" \
    && test -f /opt/ghostty-web/node_modules/ghostty-web/dist/ghostty-web.js \
    && test -f /opt/ghostty-web/node_modules/ghostty-web/ghostty-vt.wasm \
    && chmod -R a+rX /opt/ghostty-web

ENV PATH="/opt/astroai/bin:${PATH}" \
    ASTROAI_SESSION_KIND=studio \
    DSH_PORT=3080

COPY scripts/lib/skaha-proxy.sh scripts/lib/studio-canfar-proxy.py \
     scripts/lib/agent-wizard.py scripts/lib/orx-wire-compute.py \
     scripts/lib/session_title.py /opt/astroai/lib/
COPY scripts/common-init.sh scripts/startup-studio.sh /cadc/
COPY scripts/skaha-startup-studio.sh /skaha/startup.sh
RUN chmod a+rx /opt/astroai/lib/studio-canfar-proxy.py /opt/astroai/lib/agent-wizard.py \
        /opt/astroai/lib/orx-wire-compute.py /opt/astroai/lib/session_title.py \
        /cadc/*.sh /skaha/startup.sh \
    && chmod a+r /opt/astroai/lib/skaha-proxy.sh

EXPOSE 5000

# Do not use curl -f — dsh/proxy may answer 401 without ?token=.
HEALTHCHECK --interval=30s --timeout=10s --start-period=240s --retries=3 \
    CMD curl -sS -o /dev/null --max-time 5 http://127.0.0.1:5000/ || exit 1

WORKDIR /tmp
CMD ["/skaha/startup.sh"]
