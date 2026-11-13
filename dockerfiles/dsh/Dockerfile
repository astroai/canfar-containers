# syntax=docker/dockerfile:1
# AstroAI dsh: one coding-agent workbench for CANFAR.
#   Agent       /          dsh (127.0.0.1:3080)
#   Terminal    /terminal/ ghostty-web + tmux (127.0.0.1:4793), always on
#   JupyterLab  /jupyter/  (127.0.0.1:8888), started the first time its tab is opened
#   VS Code     /vscode/   OpenVSCode Server (127.0.0.1:8080), same
#   Lab         /lab/      canfar lab web (127.0.0.1:4792): agents, keys, code, cluster
# behind dsh-canfar-proxy.py on :5000. Tabs and routes: config/dsh-tabs.json.
# A simpler successor to dockerfiles/studio; renamed astroai/studio once proven.

ARG REGISTRY=images.canfar.net
ARG OWNER=astroai
ARG BASE_NAME=base
ARG TAG=local
# One pnpm for the image and every user (dsh plugin installs).
ARG PNPM_VERSION=12.6.0
# Same string as canfar-lab DSH_VERSION. Do not float the npm alpha tag.
ARG DSH_VERSION=0.2.1-rc.3
ARG GHOSTTY_WEB_VERSION=0.4.0-next.20.g1858a59
ARG NODE_PTY_VERSION=1.1.0
ARG WS_VERSION=8.21.3

# Optional canfar-lab wheel(s) newer than PyPI, e.g. an unreleased build:
#   docker buildx bake dsh --set dsh.contexts.canfar-lab-dist=../canfar-lab/dist
# Empty by default: the image keeps the PyPI canfar-lab from base.
FROM scratch AS canfar-lab-dist

FROM ${REGISTRY}/${OWNER}/${BASE_NAME}:${TAG}

ARG PNPM_VERSION
ARG DSH_VERSION
ARG GHOSTTY_WEB_VERSION
ARG NODE_PTY_VERSION
ARG WS_VERSION

LABEL io.canfar.skaha.session.type="contributed" \
      org.opencontainers.image.title="AstroAI dsh" \
      org.opencontainers.image.description="Coding agents (dsh + Claude Code, Codex, Antigravity, OpenCode, Pi, Oh My Pi) with save/restore and Ray for CANFAR" \
      org.opencontainers.image.source="https://github.com/astroai/canfar-containers"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# 1. dsh, pinned, in /opt/astroai. pnpm in a shared corepack home so plugin
#    installs use the pinned version without a per-user download.
ENV COREPACK_HOME=/opt/corepack \
    COREPACK_DEFAULT_TO_LATEST=0 \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN test -n "${DSH_VERSION}" \
    && npm install -g --prefix /opt/astroai "@deepseek-ai/dsh@${DSH_VERSION}" \
    && /opt/astroai/bin/dsh --version | grep -F -- "${DSH_VERSION}" \
    && corepack enable \
    && corepack prepare "pnpm@${PNPM_VERSION}" --activate \
    && chmod -R a+rX "${COREPACK_HOME}" \
    && pnpm --version \
    && npm cache clean --force

# Keys-only: do not force DeepSeek-only onboarding.
COPY scripts/patch-dsh-skip-deepseek-onboarding.sh /tmp/patch-dsh-skip-deepseek-onboarding.sh
RUN bash /tmp/patch-dsh-skip-deepseek-onboarding.sh /opt/astroai \
    && rm -f /tmp/patch-dsh-skip-deepseek-onboarding.sh

# The dsh plugins this image uses (astroai-settings, astroai-brand,
# opencode-session) ship inside canfar-lab; `studio --prepare` installs them
# into the profile at boot without a network fetch.

# 2. Terminal tab: ghostty-web over a persistent tmux session (tmux from base).
RUN command -v tmux \
    && printf '%s\n' \
        'set -g default-shell /bin/bash' \
        'set -g mouse on' \
        'set -g history-limit 50000' \
        'set -g status-position top' \
        'set -g status-style "bg=colour234,fg=colour246"' \
        'set -g status-left-length 20' \
        'set -g status-left "#[fg=colour117,bold] AstroAI #[default] "' \
        'set -g window-status-current-style "fg=colour189,bold"' \
        'set -g status-right ""' \
        'set -s escape-time 10' \
        > /etc/tmux.conf
COPY scripts/ghostty-web-server.mjs /opt/ghostty-web/server.mjs
COPY scripts/ghostty-web-client.mjs /opt/ghostty-web/client.mjs
RUN npm install --omit=dev --prefix /opt/ghostty-web \
        "ghostty-web@${GHOSTTY_WEB_VERSION}" \
        "@lydell/node-pty@${NODE_PTY_VERSION}" \
        "ws@${WS_VERSION}" \
    && test -f /opt/ghostty-web/node_modules/ghostty-web/dist/ghostty-web.js \
    && chmod -R a+rX /opt/ghostty-web \
    && npm cache clean --force

# 3. JupyterLab and OpenVSCode Server, started on first use (see startup-dsh.sh).
RUN uv pip install --system ipykernel jupyterlab && uv cache clean \
    && mkdir -p /usr/local/share/jupyter/lab/settings \
    && printf '%s\n' '{"@jupyterlab/apputils-extension:notification": {"fetchNews": "false"}}' \
        > /usr/local/share/jupyter/lab/settings/overrides.json
ENV JUPYTER_CONFIG_DIR=/etc/jupyter
RUN mkdir -p /etc/jupyter /etc/astroai-lab
COPY config/jupyter_server_config.py /etc/jupyter/jupyter_server_config.py
COPY config/jupyter_lab_config.py /etc/jupyter/jupyter_lab_config.py
COPY config/jupyter/astroai_scratch_env.py /etc/astroai-lab/jupyter_astroai_scratch_env.py
COPY config/ipython_kernel_config.py /etc/ipython/ipython_kernel_config.py
COPY scripts/install-openvscode-latest.sh /tmp/install-openvscode-latest.sh
ADD https://api.github.com/repos/gitpod-io/openvscode-server/releases/latest /tmp/openvscode-release.json
RUN bash /tmp/install-openvscode-latest.sh \
    && rm -f /tmp/install-openvscode-latest.sh /tmp/openvscode-release.json \
    && mkdir -p /opt/openvscode-server/data/Machine
COPY config/openvscode-settings.json /opt/openvscode-server/data/Machine/settings.json
RUN sed -i 's/AstroAI Studio/AstroAI/' /opt/openvscode-server/data/Machine/settings.json \
    && chmod a+r /opt/openvscode-server/data/Machine/settings.json

# Agents live on $SCRATCH: the Lab installs the newest version at every session
# start and on Update. Their own self-updaters would write into $HOME (/arc,
# slow and shared), so they are off. omp updates its own $SCRATCH binary.
ENV DISABLE_AUTOUPDATER=1 \
    OPENCODE_DISABLE_AUTOUPDATE=1 \
    AGY_CLI_DISABLE_AUTO_UPDATE=1 \
    PI_SKIP_VERSION_CHECK=1

# 4. canfar-lab with `canfar lab web` (the Lab tab); fails the build when the
#    installed release predates it. Late on purpose: it changes most often.
RUN --mount=from=canfar-lab-dist,target=/tmp/canfar-lab-dist \
    shopt -s nullglob; wheels=(/tmp/canfar-lab-dist/canfar_lab-*.whl); \
    if (( ${#wheels[@]} )); then \
        uv pip install --python /opt/canfar --upgrade "${wheels[@]}" \
        && chmod -R a+rwX /opt/canfar && uv cache clean; \
    fi \
    && /opt/canfar/bin/canfar-lab --version \
    && /opt/canfar/bin/canfar-lab web --help >/dev/null \
    || { echo "canfar-lab has no 'web' command: pass a newer wheel via the canfar-lab-dist context" >&2; exit 1; }

# 5. Proxy, tab table, startup.
ENV PATH="/opt/astroai/bin:/opt/openvscode-server/bin:${PATH}" \
    ASTROAI_SESSION_KIND=dsh \
    DSH_PORT=3080 \
    CANFAR_LAB_WEB_PORT=4792 \
    ASTROAI_TERMINAL_PORT=4793 \
    ASTROAI_JUPYTER_PORT=8888 \
    ASTROAI_VSCODE_PORT=8080 \
    ASTROAI_TABS_FILE=/opt/astroai/studio/tabs.json

COPY config/dsh-tabs.json /opt/astroai/studio/tabs.json
COPY scripts/lib/dsh-canfar-proxy.py scripts/lib/session_title.py /opt/astroai/lib/
COPY scripts/common-init.sh scripts/startup-dsh.sh /cadc/
COPY scripts/skaha-startup-dsh.sh /skaha/startup.sh
RUN chmod a+rx /opt/astroai/lib/dsh-canfar-proxy.py /cadc/*.sh /skaha/startup.sh \
    && chmod a+r /opt/astroai/lib/session_title.py /opt/astroai/studio/tabs.json \
    && python3 -m py_compile /opt/astroai/lib/dsh-canfar-proxy.py

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -sS -o /dev/null --max-time 5 http://127.0.0.1:5000/api/studio/status || exit 1

WORKDIR /tmp
CMD ["/skaha/startup.sh"]
