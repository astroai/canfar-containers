# syntax=docker/dockerfile:1
# AstroAI openscience: OpenScience research workspace (AI scientist) for CANFAR.
# Built from source with a base-path patch so the web workspace works under
# /session/contrib/<id>/. The server binds 127.0.0.1:4796; openscience-canfar-proxy.py
# on :5000 adds its bearer token and mounts the AstroAI hub at /astroai-agents/.

ARG REGISTRY=images.canfar.net
ARG OWNER=astroai
ARG BASE_NAME=base
ARG TAG=local
ARG OPENSCIENCE_VERSION=2.0.143
ARG BUN_VERSION=1.3.14

FROM oven/bun:${BUN_VERSION} AS openscience
ARG OPENSCIENCE_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "v${OPENSCIENCE_VERSION}" \
        https://github.com/synthetic-sciences/openscience /src
COPY patches/openscience/ /patches/
WORKDIR /src
RUN git apply /patches/*.patch
WORKDIR /src/backend/cli
RUN bun install --frozen-lockfile
RUN OPENSCIENCE_CHANNEL=latest OPENSCIENCE_WEB_BASE=./ \
        bun run script/build.ts --single --skip-install \
    && install -D -m 0755 dist/@synsci/openscience-linux-*/bin/openscience /out/openscience \
    && HOME=/tmp /out/openscience --version

FROM ${REGISTRY}/${OWNER}/${BASE_NAME}:${TAG}

ARG OPENSCIENCE_VERSION

LABEL io.canfar.skaha.session.type="contributed" \
      org.opencontainers.image.title="AstroAI OpenScience" \
      org.opencontainers.image.description="OpenScience research workspace for CANFAR" \
      org.opencontainers.image.source="https://github.com/synthetic-sciences/openscience" \
      org.opencontainers.image.version="${OPENSCIENCE_VERSION}"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Astronomy Python: OpenScience kernels and shell use the first python3 on PATH,
# and the wrapper puts this venv first.
COPY --from=openscience /out/openscience /opt/openscience/bin/openscience
COPY scripts/openscience-canfar.sh /opt/astroai/bin/openscience
COPY config/science-py.txt /tmp/science-py.txt
RUN chmod a+rx /opt/openscience/bin/openscience /opt/astroai/bin/openscience \
    && /opt/openscience/bin/openscience --version \
    && uv venv /opt/astroai/venv/science \
    && uv pip install --python /opt/astroai/venv/science -r /tmp/science-py.txt \
    && /opt/astroai/venv/science/bin/python -c \
        "import numpy, scipy, astropy, astroquery, pyvo, photutils, specutils, lightkurve, ccdproc, astroscrappy, astroplan, spectral_cube, radio_beam, dust_extinction, zero_point, emcee, dynesty, corner" \
    && chmod -R a+rwX /opt/astroai/venv/science \
    && rm /tmp/science-py.txt \
    && uv cache clean

ENV PATH="/opt/astroai/bin:${PATH}" \
    ASTROAI_SESSION_KIND=openscience

COPY scripts/lib/skaha-proxy.sh scripts/lib/openscience-canfar-proxy.py \
     scripts/lib/agent-wizard.py scripts/lib/orx-wire-compute.py \
     scripts/lib/session_title.py /opt/astroai/lib/
COPY scripts/common-init.sh scripts/startup-openscience.sh /cadc/
COPY scripts/skaha-startup-openscience.sh /skaha/startup.sh
RUN chmod a+rx /opt/astroai/lib/openscience-canfar-proxy.py /opt/astroai/lib/agent-wizard.py \
        /opt/astroai/lib/orx-wire-compute.py /opt/astroai/lib/session_title.py \
        /cadc/*.sh /skaha/startup.sh \
    && chmod a+r /opt/astroai/lib/skaha-proxy.sh

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5000/__astroai/health >/dev/null

WORKDIR /tmp
CMD ["/skaha/startup.sh"]
