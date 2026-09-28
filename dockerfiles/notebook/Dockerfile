# syntax=docker/dockerfile:1
# AstroAI notebook: minimal JupyterLab (Notebook session type, port 8888).

ARG REGISTRY=images.canfar.net
ARG OWNER=astroai
ARG BASE_NAME=base
ARG TAG=local

FROM ${REGISTRY}/${OWNER}/${BASE_NAME}:${TAG}

LABEL org.opencontainers.image.title="AstroAI Notebook" \
      org.opencontainers.image.description="JupyterLab for CANFAR Notebook sessions" \
      io.canfar.skaha.session.type="notebook"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN uv pip install --system \
    ipykernel \
    jupyterlab

ENV JUPYTER_CONFIG_DIR=/etc/jupyter

RUN mkdir -p /etc/jupyter /etc/astroai-lab
COPY config/jupyter_server_config.py /etc/jupyter/jupyter_server_config.py
COPY config/jupyter_lab_config.py /etc/jupyter/jupyter_lab_config.py
COPY config/jupyter/astroai_scratch_env.py /etc/astroai-lab/jupyter_astroai_scratch_env.py
COPY config/ipython_kernel_config.py /etc/ipython/ipython_kernel_config.py
COPY config/notebooks/ /opt/astroai/notebooks/

COPY scripts/common-init.sh scripts/startup-notebook.sh /cadc/
COPY scripts/skaha-startup-notebook.sh /skaha/startup.sh
RUN chmod a+rx /cadc/*.sh /skaha/startup.sh

EXPOSE 8888

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD bash -c 'SID="${JUPYTER_TOKEN:-}"; if [[ -n "${SID}" ]]; then curl -fsS "http://127.0.0.1:8888/session/notebook/${SID}/api/status" >/dev/null; else curl -fsS http://127.0.0.1:8888/api/status >/dev/null; fi'

WORKDIR /tmp
CMD ["/skaha/startup.sh"]
