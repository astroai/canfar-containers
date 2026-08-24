# syntax=docker/dockerfile:1
# AstroAI ghostty-web: browser shell via ghostty-web + tmux.

ARG REGISTRY=images.canfar.net
ARG OWNER=astroai
ARG BASE_NAME=base
ARG TAG=local
ARG STARSHIP_VERSION=1.26.0
ARG STARSHIP_SHA256_X64=b7c232b0e8249d8e55a40beb79c5c43a7d370f3f9408bd215deb0170daeaadf3
ARG STARSHIP_SHA256_ARM64=dc30189378d2f2e287384e8a692d3f95ad1df64cf0e8c36aa9201516028aed6b
# 0.4.0 stubs getHyperlinkUri()→null (OSC 8 links dead). next.20 wires the WASM URI API.
ARG GHOSTTY_WEB_VERSION=0.4.0-next.20.g1858a59
ARG NODE_PTY_VERSION=1.1.0
ARG WS_VERSION=8.21.3

FROM ${REGISTRY}/${OWNER}/${BASE_NAME}:${TAG}

ARG STARSHIP_VERSION
ARG STARSHIP_SHA256_X64
ARG STARSHIP_SHA256_ARM64
ARG GHOSTTY_WEB_VERSION
ARG NODE_PTY_VERSION
ARG WS_VERSION

LABEL io.canfar.skaha.session.type="contributed"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update && apt-get install -y --no-install-recommends \
    tmux \
    && rm -rf /var/lib/apt/lists/*

RUN ARCH="$(uname -m)" \
    && case "${ARCH}" in \
        x86_64) TRIPLE=x86_64-unknown-linux-musl; SHA256="${STARSHIP_SHA256_X64}" ;; \
        aarch64) TRIPLE=aarch64-unknown-linux-musl; SHA256="${STARSHIP_SHA256_ARM64}" ;; \
        *) echo "unsupported arch: ${ARCH}" >&2; exit 1 ;; \
       esac \
    && TARBALL="starship-${TRIPLE}.tar.gz" \
    && curl -fsSL -o "${TARBALL}" \
        "https://github.com/starship/starship/releases/download/v${STARSHIP_VERSION}/${TARBALL}" \
    && echo "${SHA256}  ${TARBALL}" | sha256sum -c - \
    && tar -xzf "${TARBALL}" -C /usr/local/bin \
    && rm "${TARBALL}" \
    && starship --version \
    && { printf '%s\n' \
        '# Bash-only (/etc/profile sources profile.d for all login shells, including sh).' \
        'if [ -z "${BASH_VERSION:-}" ]; then' \
        '    return 0 2>/dev/null || exit 0' \
        'fi' \
        'export STARSHIP_CONFIG=/etc/starship.toml'; \
       /usr/local/bin/starship init bash --print-full-init; \
    } > /etc/profile.d/starship.sh \
    && chmod a+r /etc/profile.d/starship.sh

RUN printf '%s\n' \
    'set -g default-shell /bin/bash' \
    'set -g mouse on' \
    'set -g history-limit 50000' \
    'set -g status-interval 5' \
    'set -g status-position top' \
    'set -g status-left-length 20' \
    'set -g status-left "#[fg=colour117,bold]AstroAI"' \
    'set -g status-right "#[fg=colour245]#{host_short}"' \
    'setw -g window-status-format " #[fg=colour245,bold]#I#[fg=colour245]:#[fg=colour252]#W "' \
    'setw -g window-status-current-format " #[fg=colour117,bold]#I#[fg=colour117]:#[fg=colour255,bold]#W "' \
    'setw -g window-status-separator ""' \
    'set -s set-clipboard on' \
    'set -g allow-passthrough on' \
    'set -g focus-events on' \
    'set -g aggressive-resize on' \
    'set -s escape-time 10' \
    'set -g base-index 1' \
    'setw -g pane-base-index 1' \
    'setw -g mode-keys vi' \
    'bind-key -T copy-mode-vi v send-keys -X begin-selection' \
    'bind-key -T copy-mode-vi y send-keys -X copy-pipe-and-cancel "/opt/astroai/bin/osc52-copy"' \
    'bind-key -T copy-mode-vi Enter send-keys -X copy-pipe-and-cancel "/opt/astroai/bin/osc52-copy"' \
    'bind-key -T copy-mode-vi MouseDragEnd1Pane send-keys -X copy-pipe-and-cancel "/opt/astroai/bin/osc52-copy"' \
    'bind-key -T copy-mode MouseDragEnd1Pane send-keys -X copy-pipe-and-cancel "/opt/astroai/bin/osc52-copy"' \
    'bind-key m set -g mouse \; display-message "mouse #{?mouse,on,off} (off = native browser select)"' \
    > /etc/tmux.conf \
    && if tmux -V | awk '{ver=$2; gsub(/[^0-9.].*/,"",ver); split(ver,a,"."); exit !(a[1]>3 || (a[1]==3 && a[2]+0>=2))}'; then \
         printf '%s\n' 'set -as terminal-features ",*:clipboard"' >> /etc/tmux.conf; \
       fi

COPY scripts/osc52-copy /opt/astroai/bin/osc52-copy
RUN chmod a+rx /opt/astroai/bin/osc52-copy

COPY config/starship.toml /etc/starship.toml
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

COPY scripts/common-init.sh scripts/startup-ghostty-web.sh /cadc/
COPY scripts/skaha-startup-ghostty-web.sh /skaha/startup.sh
RUN chmod a+rx /cadc/*.sh /skaha/startup.sh

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5000/ >/dev/null

WORKDIR /tmp
CMD ["/skaha/startup.sh"]
