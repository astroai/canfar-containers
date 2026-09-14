# syntax=docker/dockerfile:1
# AstroAI Studio: dsh web coding portal for CANFAR contributed sessions.
# Proxies :3080 → :5000 with path rewrite (see docs/STUDIO.md).

ARG REGISTRY=images.canfar.net
ARG OWNER=astroai
ARG BASE_NAME=base
ARG TAG=local
# Keep in sync with canfar-lab data/agent/agents/dsh.yaml
ARG DSH_VERSION=0.1.5-rc.2

FROM ${REGISTRY}/${OWNER}/${BASE_NAME}:${TAG}

ARG DSH_VERSION

LABEL io.canfar.skaha.session.type="contributed" \
      org.opencontainers.image.title="AstroAI Studio" \
      org.opencontainers.image.description="dsh-based coding portal for CANFAR" \
      org.opencontainers.image.source="https://github.com/deepseek-ai/deepseek-harness" \
      org.opencontainers.image.version="${DSH_VERSION}"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
# Global npm install into /opt/astroai so the binary is image-baked (not $HOME).
RUN npm install -g --prefix /opt/astroai "@deepseek-ai/dsh@${DSH_VERSION}" \
    && /opt/astroai/bin/dsh --version

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

# dsh web can take >40s on cold start; match startup wait (~60s) with margin.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5000/ >/dev/null

WORKDIR /tmp
CMD ["/skaha/startup.sh"]
