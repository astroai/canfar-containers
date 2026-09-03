# syntax=docker/dockerfile:1
# AstroAI marimo: reactive notebooks.

ARG REGISTRY=images.canfar.net
ARG OWNER=astroai
ARG BASE_NAME=base
ARG TAG=local

FROM ${REGISTRY}/${OWNER}/${BASE_NAME}:${TAG}

LABEL io.canfar.skaha.session.type="contributed"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN uv pip install --system marimo vos

COPY config/notebooks/starter.py /opt/astroai/notebooks/starter.py
COPY config/marimo.toml /opt/astroai/config/marimo.toml
COPY scripts/lib/canfar_marimo.py scripts/lib/sitecustomize.py \
     scripts/lib/astroai-html-proxy.py /opt/astroai/lib/
COPY scripts/common-init.sh scripts/startup-marimo.sh /cadc/
COPY scripts/skaha-startup-marimo.sh /skaha/startup.sh
RUN chmod a+rx /cadc/*.sh /skaha/startup.sh /opt/astroai/lib/astroai-html-proxy.py

ARG PYTHONPATH=
ENV PYTHONPATH="/opt/astroai/lib${PYTHONPATH:+:${PYTHONPATH}}"

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5000/ >/dev/null

WORKDIR /tmp
CMD ["/skaha/startup.sh"]
