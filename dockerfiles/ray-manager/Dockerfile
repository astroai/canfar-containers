# syntax=docker/dockerfile:1
# Ray Manager — fat interactive base + Ray head + contributed UI (port 5000).

ARG REGISTRY=images.canfar.net
ARG OWNER=astroai
ARG TAG=local

FROM ${REGISTRY}/${OWNER}/base:${TAG}

ARG TAG=local

LABEL io.canfar.skaha.session.type="contributed"
LABEL org.opencontainers.image.title="AstroAI Ray Manager" \
      org.opencontainers.image.version="${TAG}"

ENV RAY_IMAGE_TAG=${TAG}
ENV BUILD_TAG=${TAG}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

COPY config/ray-deps.txt config/ray-deps.lock config/astroai-lab.lock /tmp/
RUN uv venv /opt/astroai/venv/ray \
    && uv pip install --python /opt/astroai/venv/ray --requirements /tmp/ray-deps.lock \
    && uv pip install --python /opt/astroai/venv/ray --requirements /tmp/astroai-lab.lock \
    && rm /tmp/ray-deps.txt /tmp/ray-deps.lock /tmp/astroai-lab.lock \
    && /opt/astroai/venv/ray/bin/python -c "import ray; print(ray.__version__)" \
        > /opt/astroai/ray-version.txt \
    && chmod a+r /opt/astroai/ray-version.txt \
    && uv pip install --python /opt/astroai/venv/ray \
        fastapi uvicorn[standard] python-multipart httpx websockets \
    && /opt/astroai/venv/ray/bin/canfar-lab cluster --help >/dev/null \
    && /opt/astroai/venv/ray/bin/canfar-lab autoscaler --help >/dev/null \
    && printf '%s\n' \
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
        '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
        | /opt/astroai/venv/ray/bin/canfar-lab mcp serve \
        | grep -q cluster_start

# canfar-lab in this venv tracks the default branch. /opt/canfar already
# does, because it comes from the base image.
ADD https://api.github.com/repos/astroai/canfar-lab/git/refs/heads/main /tmp/canfar-lab-head.json
COPY scripts/install-canfar-lab-head.sh /tmp/install-canfar-lab-head.sh
RUN bash /tmp/install-canfar-lab-head.sh /opt/astroai/venv/ray \
    && /opt/astroai/venv/ray/bin/canfar-lab cluster --help >/dev/null \
    && /opt/astroai/venv/ray/bin/canfar-lab autoscaler --help >/dev/null \
    && printf '%s\n' \
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
        '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
        | /opt/astroai/venv/ray/bin/canfar-lab mcp serve \
        | grep -q cluster_start \
    && rm -f /tmp/install-canfar-lab-head.sh /tmp/canfar-lab-head.json

ENV PATH="/opt/astroai/venv/ray/bin:/opt/canfar/bin:${PATH}"

COPY ray/worker/start-worker.sh /opt/astroai/bin/start-ray-worker.sh
COPY scripts/ray-head-start.sh /opt/astroai/bin/ray-head-start.sh
COPY scripts/ray-network-probe.sh /opt/astroai/bin/ray-network-probe.sh
COPY scripts/lib/ray-version.sh /opt/astroai/lib/ray-version.sh
COPY scripts/lib/session_title.py /opt/astroai/lib/session_title.py
COPY examples/ray/ /opt/astroai/examples/ray/
COPY ray/manager/ /opt/astroai/ray-manager/
COPY scripts/startup-ray-manager.sh /skaha/startup.sh
RUN chmod a+rx /opt/astroai/bin/start-ray-worker.sh /opt/astroai/bin/ray-head-start.sh \
    /opt/astroai/bin/ray-network-probe.sh /opt/astroai/lib/ray-version.sh \
    /opt/astroai/lib/session_title.py \
    /skaha/startup.sh

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5000/healthz >/dev/null

WORKDIR /tmp
CMD ["/skaha/startup.sh"]
