# syntax=docker/dockerfile:1
# AstroAI Studio: Unified 5-in-1 development studio for CANFAR.
# Multiplexes DeepSeek Harness (:3080), Ghostty terminal (:4793),
# JupyterLab (:8888), Marimo (:2718), OpenVSCode (:8080), and Compute Hub (:4792)
# over public port 5000.

ARG REGISTRY=images.canfar.net
ARG OWNER=astroai
ARG BASE_NAME=base
ARG TAG=local
# One pnpm for the image and every user: a node_modules built by one pnpm
# major and changed by another fails with a layout error.
ARG PNPM_VERSION=12.6.0
ARG GHOSTTY_WEB_VERSION=0.4.0-next.20.g1858a59
ARG NODE_PTY_VERSION=1.1.0
ARG WS_VERSION=8.21.3

FROM ${REGISTRY}/${OWNER}/${BASE_NAME}:${TAG}

ARG PNPM_VERSION
ARG GHOSTTY_WEB_VERSION
ARG NODE_PTY_VERSION
ARG WS_VERSION

LABEL io.canfar.skaha.session.type="contributed" \
      org.opencontainers.image.title="AstroAI Studio" \
      org.opencontainers.image.description="Unified 5-in-1 coding studio (AI Agents, Terminal, JupyterLab, Marimo, VS Code) for CANFAR" \
      org.opencontainers.image.source="https://github.com/astroai/canfar-containers"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# 1. dsh: coding agent harness, image-baked into /opt/astroai.
# The CLI published from deepseek-harness master is the npm dist-tag `alpha`.
# `apps/cli/lib` is a build product and is not in git; npm `latest` is an older rc.
# pnpm lives in a shared corepack home so `dsh plugin` and the plugin market
# use the pinned version without a per-user download into $HOME.
ENV COREPACK_HOME=/opt/corepack \
    COREPACK_DEFAULT_TO_LATEST=0 \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0
ADD https://api.github.com/repos/deepseek-ai/deepseek-harness/git/refs/heads/master /tmp/dsh-head.json
RUN test -s /tmp/dsh-head.json \
    && npm install -g --prefix /opt/astroai "@deepseek-ai/dsh@alpha" \
    && /opt/astroai/bin/dsh --version \
    && corepack enable \
    && corepack prepare "pnpm@${PNPM_VERSION}" --activate \
    && chmod -R a+rX "${COREPACK_HOME}" \
    && pnpm --version \
    && rm -f /tmp/dsh-head.json

# Plugins baked for the Studio profile: canfar-lab copies every dependency
# here that declares a dsh bundle into ~/.dsh/profiles/astroai and enables it.
# dshmarket tracks its default branch. pnpm 12 ignores a "pnpm" key in
# package.json and waits out freshly published versions unless
# minimumReleaseAge is 0. "*" allows the alpha CLI as a peer.
ADD https://api.github.com/repos/dsh-market/dsh-market/git/refs/heads/main /tmp/dshmarket-head.json
RUN test -s /tmp/dshmarket-head.json \
    && mkdir -p /opt/astroai/dsh-plugins \
    && cd /opt/astroai/dsh-plugins \
    && printf '%s\n' \
        '{' \
        '  "name": "astroai-dsh-plugins",' \
        '  "private": true,' \
        '  "dependencies": {"dshmarket": "*"}' \
        '}' \
        > package.json \
    && printf '%s\n' \
        'nodeLinker: hoisted' \
        'minimumReleaseAge: 0' \
        'peerDependencyRules:' \
        '  allowedVersions:' \
        '    "@deepseek-ai/dsh-settings": "*"' \
        > pnpm-workspace.yaml \
    && pnpm install --prod \
    && test -f node_modules/dshmarket/cordis.patch.yml \
    && rm -rf "$(pnpm store path)" /tmp/dshmarket-head.json \
    && chmod -R a+rX /opt/astroai/dsh-plugins

# Keys-only: do not force DeepSeek-only onboarding gate
COPY scripts/patch-dsh-skip-deepseek-onboarding.sh /tmp/patch-dsh-skip-deepseek-onboarding.sh
RUN bash /tmp/patch-dsh-skip-deepseek-onboarding.sh /opt/astroai \
    && rm -f /tmp/patch-dsh-skip-deepseek-onboarding.sh

# 2. Ghostty-web terminal sidecar + tmux
RUN apt-get update && apt-get install -y --no-install-recommends tmux \
    && rm -rf /var/lib/apt/lists/* \
    && printf '%s\n' \
        'set -g default-shell /bin/bash' \
        'set -g mouse on' \
        'set -g history-limit 50000' \
        'set -g status-position top' \
        'set -g status-style "bg=colour234,fg=colour246"' \
        'set -g status-left-length 20' \
        'set -g status-left "#[fg=colour117,bold] AstroAI Studio #[default] "' \
        'set -g window-status-current-style "fg=colour189,bold"' \
        'set -g status-right ""' \
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

# 3. JupyterLab + Marimo (system-wide scientific notebook suite)
RUN uv pip install --system ipykernel jupyterlab marimo vos

ENV JUPYTER_CONFIG_DIR=/etc/jupyter
RUN mkdir -p /etc/jupyter /etc/astroai-lab
COPY config/jupyter_server_config.py /etc/jupyter/jupyter_server_config.py
COPY config/jupyter_lab_config.py /etc/jupyter/jupyter_lab_config.py
COPY config/jupyter/astroai_scratch_env.py /etc/astroai-lab/jupyter_astroai_scratch_env.py
COPY config/ipython_kernel_config.py /etc/ipython/ipython_kernel_config.py
COPY config/marimo.toml /opt/astroai/config/marimo.toml
COPY config/notebooks/ /opt/astroai/notebooks/

# 4. OpenVSCode Server (browser IDE): current GitHub release.
COPY scripts/install-openvscode-latest.sh /tmp/install-openvscode-latest.sh
ADD https://api.github.com/repos/gitpod-io/openvscode-server/releases/latest /tmp/openvscode-release.json
RUN bash /tmp/install-openvscode-latest.sh \
    && rm -f /tmp/install-openvscode-latest.sh /tmp/openvscode-release.json

RUN mkdir -p /opt/openvscode-server/data/Machine
COPY config/openvscode-settings.json /opt/openvscode-server/data/Machine/settings.json
RUN chmod a+w /opt/openvscode-server/data/Machine/settings.json

ENV PATH="/opt/astroai/bin:/opt/openvscode-server/bin:${PATH}" \
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

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -sS -o /dev/null --max-time 5 http://127.0.0.1:5000/api/studio/status || exit 1

WORKDIR /tmp
CMD ["/skaha/startup.sh"]
