# syntax=docker/dockerfile:1
# AstroAI Studio: Unified 5-in-1 development studio for CANFAR.
# Multiplexes DeepSeek Harness (:3080), Ghostty terminal (:4793),
# JupyterLab (:8888), Marimo (:2718), OpenVSCode (:8080), and Compute Hub (:4792)
# over public port 5000.

ARG REGISTRY=images.canfar.net
ARG OWNER=astroai
ARG BASE_NAME=base
ARG TAG=local
# Always build with the latest DSH release by default; invalidate with DSH_CACHEBUST
ARG DSH_VERSION=latest
ARG DSH_CACHEBUST=1
ARG GHOSTTY_WEB_VERSION=0.4.0-next.20.g1858a59
ARG NODE_PTY_VERSION=1.1.0
ARG WS_VERSION=8.21.3
ARG OPENVSCODE_VERSION=openvscode-server-v1.109.5
ARG OPENVSCODE_SHA256_X64=b433bf4f0227321a7014d8460d10a8f958adc0f45aa79bd889e84e65e8f88363
ARG OPENVSCODE_SHA256_ARM64=36d9c14036489b63de84ebace837fcacf7e60e669a0dc715802c5443684ea4dc

FROM ${REGISTRY}/${OWNER}/${BASE_NAME}:${TAG}

ARG DSH_VERSION
ARG DSH_CACHEBUST
ARG GHOSTTY_WEB_VERSION
ARG NODE_PTY_VERSION
ARG WS_VERSION
ARG OPENVSCODE_VERSION
ARG OPENVSCODE_SHA256_X64
ARG OPENVSCODE_SHA256_ARM64

LABEL io.canfar.skaha.session.type="contributed" \
      org.opencontainers.image.title="AstroAI Studio" \
      org.opencontainers.image.description="Unified 5-in-1 coding studio (AI Agents, Terminal, JupyterLab, Marimo, VS Code) for CANFAR" \
      org.opencontainers.image.source="https://github.com/astroai/canfar-containers"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# 1. dsh: Coding agent harness (latest release, image-baked into /opt/astroai)
RUN npm install -g --prefix /opt/astroai "@deepseek-ai/dsh@${DSH_VERSION}" \
    && /opt/astroai/bin/dsh --version \
    && corepack enable \
    && corepack prepare pnpm@9.15.9 --activate \
    && pnpm --version

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

# 3. JupyterLab + Marimo (system-wide scientific notebook suite)
RUN uv pip install --system ipykernel jupyterlab marimo vos

ENV JUPYTER_CONFIG_DIR=/etc/jupyter
RUN mkdir -p /etc/jupyter /etc/astroai-lab
COPY config/jupyter_server_config.py /etc/jupyter/jupyter_server_config.py
COPY config/jupyter_lab_config.py /etc/jupyter/jupyter_lab_config.py
COPY config/jupyter/astroai_scratch_env.py /etc/astroai-lab/jupyter_astroai_scratch_env.py
COPY config/marimo.toml /opt/astroai/config/marimo.toml
COPY config/notebooks/ /opt/astroai/notebooks/

# 4. OpenVSCode Server (browser IDE)
RUN ARCH=$(uname -m | sed 's/x86_64/x64/; s/aarch64/arm64/') \
    && case "${ARCH}" in \
        x64) SHA256="${OPENVSCODE_SHA256_X64}" ;; \
        arm64) SHA256="${OPENVSCODE_SHA256_ARM64}" ;; \
        *) echo "unsupported arch: ${ARCH}" >&2; exit 1 ;; \
       esac \
    && TARBALL="${OPENVSCODE_VERSION}-linux-${ARCH}.tar.gz" \
    && curl -fsSL -o "${TARBALL}" \
        "https://github.com/gitpod-io/openvscode-server/releases/download/${OPENVSCODE_VERSION}/${TARBALL}" \
    && echo "${SHA256}  ${TARBALL}" | sha256sum -c - \
    && tar -xzf "${TARBALL}" -C /opt \
    && mv "/opt/${OPENVSCODE_VERSION}-linux-${ARCH}" /opt/openvscode-server \
    && rm "${TARBALL}" \
    && chmod -R a+rx /opt/openvscode-server

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
