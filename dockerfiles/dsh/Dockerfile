# syntax=docker/dockerfile:1
# AstroAI dsh: one coding-agent workbench for CANFAR.
#   Agent tab  /      dsh (127.0.0.1:3080), with its own terminal for agent CLIs
#   Lab tab    /lab/  canfar lab web (127.0.0.1:4792): agents, keys, save/restore, cluster
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

# Optional canfar-lab wheel(s) newer than PyPI, e.g. an unreleased build:
#   docker buildx bake dsh --set dsh.contexts.canfar-lab-dist=../canfar-lab/dist
# Empty by default: the image keeps the PyPI canfar-lab from base.
FROM scratch AS canfar-lab-dist

FROM ${REGISTRY}/${OWNER}/${BASE_NAME}:${TAG}

ARG PNPM_VERSION
ARG DSH_VERSION

LABEL io.canfar.skaha.session.type="contributed" \
      org.opencontainers.image.title="AstroAI dsh" \
      org.opencontainers.image.description="Coding agents (dsh + Claude Code, Codex, Antigravity, OpenCode, Pi, Oh My Pi) with save/restore and Ray for CANFAR" \
      org.opencontainers.image.source="https://github.com/astroai/canfar-containers"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# 1. canfar-lab with `canfar lab web` (the Lab tab). Fails the build when the
#    installed release predates it.
RUN --mount=from=canfar-lab-dist,target=/tmp/canfar-lab-dist \
    shopt -s nullglob; wheels=(/tmp/canfar-lab-dist/canfar_lab-*.whl); \
    if (( ${#wheels[@]} )); then \
        uv pip install --python /opt/canfar --upgrade "${wheels[@]}" \
        && chmod -R a+rwX /opt/canfar && uv cache clean; \
    fi \
    && /opt/canfar/bin/canfar-lab --version \
    && /opt/canfar/bin/canfar-lab web --help >/dev/null \
    || { echo "canfar-lab has no 'web' command: pass a newer wheel via the canfar-lab-dist context" >&2; exit 1; }

# 2. dsh, pinned, in /opt/astroai. pnpm in a shared corepack home so plugin
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

# 3. Proxy, tab table, startup.
ENV PATH="/opt/astroai/bin:${PATH}" \
    ASTROAI_SESSION_KIND=dsh \
    DSH_PORT=3080 \
    CANFAR_LAB_WEB_PORT=4792 \
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
