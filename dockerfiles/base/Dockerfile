# syntax=docker/dockerfile:1
# AstroAI base: platform CLI + monitoring + compression. No browser UI.
#
# Apt packages: root-only tools not practical via pixi/uv/micromamba.
# GCC + Rust + autotools, common astronomy headers; heavy ML stacks stay in pixi.

ARG PYTHON_VERSION=3.13

FROM astroai-python:${PYTHON_VERSION}

LABEL org.opencontainers.image.title="AstroAI Base" \
      org.opencontainers.image.description="Headless dev environment for CANFAR" \
      io.canfar.skaha.session.type="headless"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Keep apt layers lean: never install docs/man/info (dpkg excludes them).
RUN printf '%s\n' \
      'path-exclude=/usr/share/doc/*' \
      'path-include=/usr/share/doc/*/copyright' \
      'path-exclude=/usr/share/man/*' \
      'path-exclude=/usr/share/info/*' \
      'path-exclude=/usr/share/lintian/*' \
      > /etc/dpkg/dpkg.cfg.d/01-nodoc \
    && apt-get update && apt-get install -y --no-install-recommends \
    acl \
    autoconf \
    automake \
    bash-completion \
    bat \
    bind9-dnsutils \
    bison \
    bsdextrautils \
    build-essential \
    bzip2 \
    ca-certificates \
    cargo \
    cmake \
    curl \
    emacs-nox \
    fd-find \
    file \
    flex \
    fzf \
    gfortran \
    gh \
    git \
    git-delta \
    git-lfs \
    htop \
    hyperfine \
    iproute2 \
    jq \
    less \
    libcfitsio-dev \
    libfftw3-dev \
    libgsl-dev \
    libtool \
    locales \
    lsof \
    make \
    nano \
    ncdu \
    ninja-build \
    nvtop \
    openssh-client \
    patch \
    pigz \
    pkg-config \
    procps \
    ripgrep \
    rsync \
    rustc \
    shellcheck \
    tealdeer \
    tree \
    libmagic1 \
    universal-ctags \
    unzip \
    vim \
    wget \
    xxd \
    xz-utils \
    zip \
    zstd \
    && rm -rf /var/lib/apt/lists/* \
    && sed -i '/en_US.UTF-8 UTF-8/s/^# //g' /etc/locale.gen \
    && locale-gen en_US.UTF-8 \
    && git lfs install --system \
    && ln -sf /usr/bin/batcat /usr/local/bin/bat \
    && ln -sf /usr/bin/fdfind /usr/local/bin/fd \
    && git config --system core.pager delta \
    && git config --system interactive.diffFilter 'delta --color-only' \
    && git config --system delta.navigate true

# Node.js LTS (official tarball + sha256 supply-chain check) so every session
# image ships node/npm/npx on PATH — the single Node version across images.
# x86_64-pinned (bake is single-arch amd64; no platform decls in docker-bake.hcl).
# Bump NODE_LTS_VERSION + NODE_LTS_SHA256 together (SHASUMS256.txt on nodejs.org).
ARG NODE_LTS_VERSION=24.19.0
ARG NODE_LTS_SHA256=14b342e71204f811bde6153be8e04b62aef63c236fef92b55f9c83154b409647
RUN curl -fsSL -o /tmp/node.tar.xz \
      "https://nodejs.org/dist/v${NODE_LTS_VERSION}/node-v${NODE_LTS_VERSION}-linux-x64.tar.xz" \
    && echo "${NODE_LTS_SHA256}  /tmp/node.tar.xz" | sha256sum -c - \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
    && rm /tmp/node.tar.xz \
    && node --version \
    && npm --version \
    && npx --version

# Markdown TUIs — pinned GitHub releases (not in Debian apt). Bump VERSION +
# SHA256 together. x86_64-only (bake is single-arch amd64).
ARG GLOW_VERSION=3.0.0
ARG GLOW_SHA256=13e05e4b2acc18d2aee44291aefe6325b077ec321b631a0cfa780e8e3bc33f78
ARG MDCAT_VERSION=2.7.1
ARG MDCAT_SHA256=a5610f76a8f3ca1da84326ee42f4e7b00c7daa349ad831168e71c3fbb054dae2
ARG OV_VERSION=0.54.0
ARG OV_SHA256=f26ef7b180dbc6bb07677f31d0a73629528b30624cb6203919f23dc6d54f9797
RUN curl -fsSL -o /tmp/glow.tar.gz \
      "https://github.com/charmbracelet/glow/releases/download/v${GLOW_VERSION}/glow_${GLOW_VERSION}_Linux_x86_64.tar.gz" \
    && echo "${GLOW_SHA256}  /tmp/glow.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/glow.tar.gz -C /tmp \
    && install -m 755 "/tmp/glow_${GLOW_VERSION}_Linux_x86_64/glow" /usr/local/bin/glow \
    && rm -rf /tmp/glow.tar.gz "/tmp/glow_${GLOW_VERSION}_Linux_x86_64" \
    && curl -fsSL -o /tmp/mdcat.tar.gz \
      "https://github.com/swsnr/mdcat/releases/download/mdcat-${MDCAT_VERSION}/mdcat-${MDCAT_VERSION}-x86_64-unknown-linux-gnu.tar.gz" \
    && echo "${MDCAT_SHA256}  /tmp/mdcat.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/mdcat.tar.gz -C /tmp \
    && install -m 755 "/tmp/mdcat-${MDCAT_VERSION}-x86_64-unknown-linux-gnu/mdcat" /usr/local/bin/mdcat \
    && ln -sf mdcat /usr/local/bin/mdless \
    && rm -rf /tmp/mdcat.tar.gz "/tmp/mdcat-${MDCAT_VERSION}-x86_64-unknown-linux-gnu" \
    && curl -fsSL -o /tmp/ov.zip \
      "https://github.com/noborus/ov/releases/download/v${OV_VERSION}/ov_${OV_VERSION}_linux_amd64.zip" \
    && echo "${OV_SHA256}  /tmp/ov.zip" | sha256sum -c - \
    && unzip -q /tmp/ov.zip -d /tmp \
    && install -m 755 /tmp/ov /usr/local/bin/ov \
    && rm -f /tmp/ov.zip /tmp/ov /tmp/ov.yaml /tmp/ov-less.yaml /tmp/LICENSE /tmp/README.md \
    && glow --version \
    && mdcat --version \
    && ov --version

ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    TERM=xterm-256color \
    SHELL=/bin/bash \
    WORK=/srcdir \
    SCRATCH=/scratch

RUN printf '%s\n' \
    '[ -f /etc/profile.d/astroai.sh ] && . /etc/profile.d/astroai.sh' \
    >> /etc/bash.bashrc

COPY scripts/astroai-profile.sh /etc/profile.d/astroai.sh
RUN chmod a+r /etc/profile.d/astroai.sh

RUN mkdir -p /opt/astroai/{bin,cache,lib,venv} /cadc "${WORK}" \
    && chmod -R a+rX /opt/astroai \
    && chmod -R a+rwx /opt/astroai/cache \
    && chmod 1777 "${WORK}"  # /srcdir stays writable (clone --dir /srcdir)

COPY config/cadc-tools.txt /opt/astroai/cadc-tools.txt
COPY scripts/patch-clint-py313.sh /tmp/patch-clint-py313.sh
COPY config/astroai-lab.in config/astroai-lab.lock /tmp/
RUN uv venv /opt/astroai/venv/cadc \
    && uv pip install --python /opt/astroai/venv/cadc -r /opt/astroai/cadc-tools.txt \
    && uv pip install --python /opt/astroai/venv/cadc --requirements /tmp/astroai-lab.lock \
    && /opt/astroai/venv/cadc/bin/astroai cluster --help >/dev/null \
    && printf '%s\n' \
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
        '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
        | /opt/astroai/venv/cadc/bin/astroai mcp serve \
        | grep -q cluster_start \
    && bash /tmp/patch-clint-py313.sh /opt/astroai/venv/cadc \
    && rm /tmp/patch-clint-py313.sh /tmp/astroai-lab.in /tmp/astroai-lab.lock \
    && chmod -R a+rwX /opt/astroai/venv/cadc \
    # Copy the packaged session shell files straight out of the installed
    # astroai-lab wheel — astroai-lab itself stays an in-session tool only.
    && mkdir -p /etc/astroai-lab \
    && /opt/astroai/venv/cadc/bin/python -c "import importlib.resources as r, shutil; p=r.files('astroai_lab.data.shell'); shutil.copy(p.joinpath('profile.sh'), '/etc/astroai-lab/profile.sh'); shutil.copy(p.joinpath('hooks.sh'), '/etc/astroai-lab/hooks.sh')" \
    && chmod a+r /etc/astroai-lab/profile.sh /etc/astroai-lab/hooks.sh \
    && test -f /etc/astroai-lab/profile.sh \
    && test -f /etc/astroai-lab/hooks.sh \
    && HOME=/tmp WORK=/srcdir SCRATCH=/scratch \
       /opt/astroai/venv/cadc/bin/astroai env export --no-ensure >/dev/null \
    && uv cache clean

COPY scripts/lib/astroai-env-common.sh scripts/lib/astroai-ui.sh scripts/lib/skaha-proxy.sh \
     scripts/lib/session_title.py /opt/astroai/lib/
COPY scripts/canfar-verify.sh scripts/canfar-verify-agents.sh scripts/bootstrap-canfar-registry.sh scripts/bootstrap-ray-manager-env.sh scripts/upgrade-cadc-tools.sh scripts/peek /opt/astroai/bin/
RUN chmod a+rx /opt/astroai/bin/* /opt/astroai/lib/* \
    && ln -sf /opt/astroai/bin/peek /usr/local/bin/peek

COPY docs/USAGE.md /opt/astroai/USAGE.md
