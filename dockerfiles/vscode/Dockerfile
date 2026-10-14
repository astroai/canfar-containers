# syntax=docker/dockerfile:1
# AstroAI vscode: OpenVSCode Server in the browser.

ARG REGISTRY=images.canfar.net
ARG OWNER=astroai
ARG BASE_NAME=base
ARG TAG=local

FROM ${REGISTRY}/${OWNER}/${BASE_NAME}:${TAG}

LABEL io.canfar.skaha.session.type="contributed"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV SHELL=/bin/bash

COPY scripts/install-openvscode-latest.sh /tmp/install-openvscode-latest.sh
ADD https://api.github.com/repos/gitpod-io/openvscode-server/releases/latest /tmp/openvscode-release.json
RUN bash /tmp/install-openvscode-latest.sh \
    && rm -f /tmp/install-openvscode-latest.sh /tmp/openvscode-release.json

RUN mkdir -p /opt/openvscode-server/data/Machine
COPY config/openvscode-settings.json /opt/openvscode-server/data/Machine/settings.json
# Session user is not root; startup patches window.title to the CANFAR session name.
RUN chmod a+w /opt/openvscode-server/data/Machine/settings.json

COPY scripts/common-init.sh scripts/startup-vscode.sh /cadc/
COPY scripts/skaha-startup-vscode.sh /skaha/startup.sh
RUN chmod a+rx /cadc/*.sh /skaha/startup.sh

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5000/ >/dev/null

WORKDIR /tmp
CMD ["/skaha/startup.sh"]
