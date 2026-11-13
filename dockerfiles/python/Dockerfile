# syntax=docker/dockerfile:1
# AstroAI Python foundation: Debian slim + uv + pixi (system-wide paths).
# uv/pixi: official install scripts (latest at build time).

ARG PYTHON_VERSION=3.13
# Single shared interpreter for base, ray-*, improc, specproc (and studios).
# Stay on 3.13 until CADC/clint is clean on 3.14. Ray ships cp313 wheels.
# uv/pixi/micromamba are latest-at-build below. Never let uv download a second
# Python into the image — that would fork ABIs and bloat every child.

FROM python:${PYTHON_VERSION}-slim

# Re-declare after FROM so ENV can see the bake/Make value.
ARG PYTHON_VERSION=3.13

LABEL org.opencontainers.image.title="AstroAI Python Runtime" \
      org.opencontainers.image.vendor="AstroAI"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update && apt-get install -y --no-install-recommends \
    bzip2 \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Bake/Make PYTHON_VERSION is the only supported minor. Child images assert
# science/Ray venvs match this (see scripts/lib/assert-image-python.sh).
ENV ASTROAI_PYTHON_VERSION=${PYTHON_VERSION} \
    UV_PYTHON=${PYTHON_VERSION} \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_MANAGED_PYTHON=1 \
    UV_TOOL_DIR=/usr/local/share/uv/tools \
    UV_TOOL_BIN_DIR=/usr/local/bin \
    PIXI_HOME=/usr/local/share/pixi \
    PIXI_BIN_DIR=/usr/local/bin \
    PIXI_CACHE_DIR=/usr/local/share/pixi/cache
# ^ UV_PYTHON_DOWNLOADS=never keeps one system CPython (the official image).
# Session users: /etc/astroai-lab/profile.sh via canfar-lab env export overrides
# runtime and cache dirs to scratch when /scratch is mounted.

ENV MAMBA_ROOT_PREFIX=/usr/local/share/micromamba \
    MAMBA_PKGS_DIRS=/usr/local/share/micromamba/pkgs
# ^ build-time paths only. Session users: canfar-lab env export sets MAMBA_ROOT_PREFIX
# and MAMBA_PKGS_DIRS under scratch when /scratch is mounted.

RUN curl -LsSf https://astral.sh/uv/install.sh \
    | env UV_UNMANAGED_INSTALL=/usr/local/bin sh \
    && curl -fsSL https://pixi.sh/install.sh \
    | env PIXI_HOME=/usr/local/share/pixi PIXI_BIN_DIR=/usr/local/bin bash \
    && ARCH="$(uname -m)" \
    && case "${ARCH}" in \
        x86_64) MAMBA_ARCH=linux-64 ;; \
        aarch64) MAMBA_ARCH=linux-aarch64 ;; \
        *) echo "unsupported arch: ${ARCH}" >&2; exit 1 ;; \
       esac \
    && curl -Ls "https://micro.mamba.pm/api/micromamba/${MAMBA_ARCH}/latest" \
        | tar -xvj -C /usr/local/bin --strip-components=1 bin/micromamba \
    && ln -sf micromamba /usr/local/bin/mamba \
    && uv --version \
    && pixi --version \
    && micromamba --version \
    && command -v uvx >/dev/null \
    && python3 -c "import os, sys; w=os.environ['ASTROAI_PYTHON_VERSION']; assert f'{sys.version_info[0]}.{sys.version_info[1]}'==w, (sys.version, w)"
