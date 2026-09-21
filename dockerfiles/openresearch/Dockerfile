# syntax=docker/dockerfile:1
# AstroAI openresearch: OpenResearch (orx) autoresearch dashboard.
# Installs the pinned upstream alphaXiv release musl binary — the Ray Jobs
# compute backend ships upstream since v0.1.88 (#141), so no fork build.
# Proxies :4791 → :5000; embeds ghostty-web at /astroai-terminal/.

ARG REGISTRY=images.canfar.net
ARG OWNER=astroai
ARG BASE_NAME=base
ARG TAG=local
# Pinned upstream release. Bump ORX_SHA256 together with ORX_VERSION
# (sha256 of openresearch-cli-x86_64-unknown-linux-musl.tar.xz).
ARG ORX_VERSION=v0.2.4
ARG ORX_SHA256=d86d6b5516b9274ed180911ba30cff0640f543e48115b2d58ef100d2a0121fd1
# Keep in sync with dockerfiles/terminal/Dockerfile.
ARG GHOSTTY_WEB_VERSION=0.4.0-next.20.g1858a59
ARG NODE_PTY_VERSION=1.1.0
ARG WS_VERSION=8.21.3

FROM ${REGISTRY}/${OWNER}/${BASE_NAME}:${TAG}

ARG ORX_VERSION
ARG ORX_SHA256
ARG GHOSTTY_WEB_VERSION
ARG NODE_PTY_VERSION
ARG WS_VERSION

LABEL io.canfar.skaha.session.type="contributed" \
      org.opencontainers.image.title="AstroAI OpenResearch" \
      org.opencontainers.image.description="OpenResearch (orx) autoresearch dashboard for CANFAR" \
      org.opencontainers.image.source="https://github.com/alphaXiv/OpenResearch" \
      org.opencontainers.image.version="${ORX_VERSION}"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
# Verify with the upstream filename — release .sha256 assets pin it.
RUN TARBALL="openresearch-cli-x86_64-unknown-linux-musl.tar.xz"; \
    curl -fsSL \
      "https://github.com/alphaXiv/openresearch-cli/releases/download/${ORX_VERSION}/${TARBALL}" \
      -o "/tmp/${TARBALL}" \
    && printf '%s *%s\n' "${ORX_SHA256}" "${TARBALL}" > /tmp/orx.sha256 \
    && cd /tmp && sha256sum -c orx.sha256 \
    && tar -xJf "/tmp/${TARBALL}" \
    && install -m 0755 openresearch-cli-x86_64-unknown-linux-musl/orx /opt/astroai/bin/orx \
    && rm -rf "/tmp/${TARBALL}" /tmp/orx.sha256 /tmp/openresearch-cli-x86_64-unknown-linux-musl \
    && /opt/astroai/bin/orx --version

# ghostty-web sidecar (same stack as astroai/terminal; proxy mounts /astroai-terminal/).
RUN apt-get update && apt-get install -y --no-install-recommends tmux \
    && rm -rf /var/lib/apt/lists/* \
    && printf '%s\n' \
        'set -g default-shell /bin/bash' \
        'set -g mouse on' \
        'set -g history-limit 50000' \
        'set -g status-position top' \
        'set -g status-left "#[fg=colour117,bold]AstroAI "' \
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
    ORX_NO_UPDATE_CHECK=1 \
    ASTROAI_SESSION_KIND=openresearch

COPY scripts/lib/skaha-proxy.sh scripts/lib/orx-canfar-proxy.py \
     scripts/lib/agent-wizard.py scripts/lib/orx-wire-compute.py \
     scripts/lib/session_title.py /opt/astroai/lib/
COPY scripts/common-init.sh scripts/startup-openresearch.sh /cadc/
COPY scripts/skaha-startup-openresearch.sh /skaha/startup.sh
RUN chmod a+rx /opt/astroai/lib/orx-canfar-proxy.py /opt/astroai/lib/agent-wizard.py \
        /opt/astroai/lib/orx-wire-compute.py /opt/astroai/lib/session_title.py \
        /cadc/*.sh /skaha/startup.sh \
    && chmod a+r /opt/astroai/lib/skaha-proxy.sh

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5000/ >/dev/null

WORKDIR /tmp
CMD ["/skaha/startup.sh"]
