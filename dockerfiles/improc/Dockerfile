# syntax=docker/dockerfile:1
# AstroAI improc: astronomy FITS/HDF5 image-processing CLIs on base.
# Headless. Apt for native CLIs; source/binaries where Debian lags; one uv
# venv (Python 3.13, CUDA 12.9) for science Python, including MaxiMask,
# torchfits, scarlet2, and ngmix. SkyMaker and sourcextractor++ are built
# from source. Versions float at build time — no lockfiles, no pin ARGs.

ARG REGISTRY=images.canfar.net
ARG OWNER=astroai
ARG TAG=local

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
FROM debian:trixie-slim AS casutools-build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libcfitsio-dev wcslib-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /tmp
COPY dockerfiles/improc/vendor/casutools-1.0.43.tar.gz /tmp/
RUN tar xzf casutools-1.0.43.tar.gz \
    && cd casutools-1.0.43 \
    && ./configure --prefix=/usr/local \
    && make -j"$(nproc)" \
    && make install \
    && { strip --strip-unneeded /usr/local/bin/* 2>/dev/null || true; } \
    && rm -rf /tmp/casutools*

FROM debian:trixie-slim AS gnuastro-build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates curl \
    libcfitsio-dev libgsl-dev libfftw3-dev libgit2-dev libjpeg-dev \
    libtiff-dev zlib1g-dev libtool-bin wcslib-dev libcurl4-openssl-dev \
    libbz2-dev liblzma-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /tmp
RUN VER=$(curl -fsSL https://ftp.gnu.org/gnu/gnuastro/ \
      | grep -oE 'gnuastro-[0-9]+(\.[0-9]+)+\.tar\.gz' \
      | sed 's/^gnuastro-//;s/\.tar\.gz$//' | sort -V | tail -1) \
    && test -n "$VER" \
    && curl -fsSL -o gnuastro.tar.gz \
      "https://ftp.gnu.org/gnu/gnuastro/gnuastro-${VER}.tar.gz" \
    && tar xzf gnuastro.tar.gz \
    && cd "gnuastro-${VER}" \
    && ./configure --prefix=/usr/local \
    && make -j"$(nproc)" \
    && make install \
    && { strip --strip-unneeded /usr/local/bin/* /usr/local/lib/*.so* 2>/dev/null || true; } \
    && rm -rf /tmp/gnuastro*

FROM debian:trixie-slim AS stuff-build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential autoconf automake libtool ca-certificates curl \
    libcfitsio-dev wcslib-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /tmp
RUN curl -fsSL -o stuff.tar.gz \
      https://github.com/astromatic/stuff/archive/refs/heads/master.tar.gz \
    && tar xzf stuff.tar.gz \
    && cd "$(ls -d stuff-* | head -1)" \
    && autoreconf -i \
    && ./configure --prefix=/usr/local \
    && make -j"$(nproc)" \
    && make install \
    && { strip --strip-unneeded /usr/local/bin/* 2>/dev/null || true; } \
    && rm -rf /tmp/stuff*

FROM debian:trixie-slim AS skymaker-build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential autoconf automake libtool ca-certificates curl pkg-config \
    libcfitsio-dev libfftw3-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /tmp
RUN curl -fsSL -o skymaker.tar.gz \
      https://github.com/astromatic/skymaker/archive/refs/heads/master.tar.gz \
    && tar xzf skymaker.tar.gz \
    && cd "$(ls -d skymaker-* | head -1)" \
    && autoreconf -i \
    && ./configure --prefix=/usr/local \
    && make -j"$(nproc)" \
    && make install \
    && { strip --strip-unneeded /usr/local/bin/* 2>/dev/null || true; } \
    && rm -rf /tmp/skymaker*

FROM debian:trixie-slim AS fits2idia-build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates cmake curl \
    libcfitsio-dev libhdf5-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /tmp
RUN TAG=$(curl -fsSL https://api.github.com/repos/CARTAvis/fits2idia/releases/latest \
      | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1) \
    && test -n "$TAG" \
    && curl -fsSL -o fits2idia.tar.gz \
      "https://github.com/CARTAvis/fits2idia/archive/refs/tags/${TAG}.tar.gz" \
    && tar xzf fits2idia.tar.gz \
    && cd "fits2idia-${TAG#v}" \
    && mkdir build && cd build \
    && cmake -DCMAKE_INSTALL_PREFIX=/usr/local .. \
    && make -j"$(nproc)" \
    && make install \
    && { strip --strip-unneeded /usr/local/bin/* /usr/local/lib/*.so* 2>/dev/null || true; } \
    && rm -rf /tmp/fits2idia*

# ---------------------------------------------------------------------------
# Final image
# ---------------------------------------------------------------------------
FROM ${REGISTRY}/${OWNER}/base:${TAG}

LABEL org.opencontainers.image.title="AstroAI Improc" \
      org.opencontainers.image.description="Astronomy image-processing CLIs (FITS/HDF5)" \
      io.canfar.skaha.session.type="headless"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Native CLIs (Debian trixie). Prefer apt when versions track upstream.
# Keep apt layers lean: never install docs/man/info (dpkg excludes them).
RUN printf '%s\n' \
      'path-exclude=/usr/share/doc/*' \
      'path-include=/usr/share/doc/*/copyright' \
      'path-exclude=/usr/share/man/*' \
      'path-exclude=/usr/share/info/*' \
      'path-exclude=/usr/share/lintian/*' \
      > /etc/dpkg/dpkg.cfg.d/01-nodoc \
    && apt-get update && apt-get install -y --no-install-recommends \
    source-extractor \
    swarp \
    psfex \
    scamp \
    stiff \
    missfits \
    fitscut \
    fitspng \
    fitsh \
    fitsverify \
    libcfitsio-bin \
    iraf \
    iraf-noao \
    funtools \
    montage \
    montage-gridtools \
    munipack \
    qfits-tools \
    wcstools \
    weightwatcher \
    theli \
    astrometry.net \
    hdf5-tools \
    h5utils \
    default-jre-headless \
    imagemagick \
    libmagickcore-7.q16-10-extra \
    unzip \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/source-extractor /usr/local/bin/sextractor \
    && if [[ -x /usr/bin/SWarp ]] && [[ ! -e /usr/local/bin/swarp ]]; then \
         ln -sf /usr/bin/SWarp /usr/local/bin/swarp; \
       fi \
    && if [[ -x /usr/bin/swarp ]] && [[ ! -e /usr/local/bin/SWarp ]]; then \
         ln -sf /usr/bin/swarp /usr/local/bin/SWarp; \
       fi

# Source-built tools
COPY --from=casutools-build /usr/local/ /usr/local/
COPY --from=gnuastro-build /usr/local/ /usr/local/
COPY --from=fits2idia-build /usr/local/ /usr/local/
COPY --from=stuff-build /usr/local/ /usr/local/
COPY --from=skymaker-build /usr/local/ /usr/local/
RUN ldconfig \
    && ln -sfn /usr/local/bin/sky /usr/local/bin/skymaker

# GALFIT binary (+ legacy ncurses5 for the Carnegie build).
# Vendored under dockerfiles/improc/vendor/ — Carnegie HTTPS is too flaky/slow
# for CI and local builds. Upstream ships one binary; no version float.
# Upstream: https://users.obs.carnegiescience.edu/peng/work/galfit/galfit3-debian64.tar.gz
COPY dockerfiles/improc/vendor/galfit3-debian64.tar.gz /tmp/
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && cd /tmp \
    && curl -fsSL --connect-timeout 30 --max-time 120 \
         -o libtinfo5_6.4-4_amd64.deb \
         http://deb.debian.org/debian/pool/main/n/ncurses/libtinfo5_6.4-4_amd64.deb \
    && curl -fsSL --connect-timeout 30 --max-time 120 \
         -o libncurses5_6.4-4_amd64.deb \
         http://deb.debian.org/debian/pool/main/n/ncurses/libncurses5_6.4-4_amd64.deb \
    && apt-get install -y --no-install-recommends \
         ./libtinfo5_6.4-4_amd64.deb ./libncurses5_6.4-4_amd64.deb \
    && tar xzf galfit3-debian64.tar.gz \
    && install -m 755 galfit /usr/local/bin/galfit \
    && rm -rf /tmp/galfit* /tmp/libtinfo5*.deb /tmp/libncurses5*.deb \
    && rm -rf /var/lib/apt/lists/*

# Imfit — prefer newest listed linux-64 binary; fall back to 1.9.0 if the
# directory index is empty/unavailable (MPE often returns no listing).
# Tarball is named imfit-X.Y.Z-linux-64.tar.gz but unpacks to imfit-X.Y.Z/.
RUN cd /opt \
    && NAME=$(curl -fsSL -A Mozilla \
         https://www.mpe.mpg.de/~erwin/resources/imfit/binaries/ 2>/dev/null \
         | grep -oE 'imfit-[0-9.]+-linux-64\.tar\.gz' | sort -V | tail -1 || true) \
    && NAME=${NAME:-imfit-1.9.0-linux-64.tar.gz} \
    && wget -q "https://www.mpe.mpg.de/~erwin/resources/imfit/binaries/${NAME}" \
    && tar xzf "${NAME}" \
    && rm "${NAME}" \
    && DIR=$(echo imfit-[0-9]* | awk '{print $1}') \
    && test -x "/opt/${DIR}/imfit" \
    && ln -sfn "${DIR}" /opt/imfit \
    && for f in imfit imfit-mcmc makeimage; do \
         ln -sf "/opt/imfit/${f}" "/usr/local/bin/${f}"; \
       done

# pqrs (Parquet CLI) — GitHub latest linux x86_64 asset
RUN cd /tmp \
    && TAG=$(curl -fsSL https://api.github.com/repos/manojkarthick/pqrs/releases/latest \
         | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1) \
    && test -n "$TAG" \
    && VER="${TAG#v}" \
    && wget -q "https://github.com/manojkarthick/pqrs/releases/download/${TAG}/pqrs-${VER}-x86_64-unknown-linux-gnu.zip" \
    && unzip -q "pqrs-${VER}-x86_64-unknown-linux-gnu.zip" \
    && install -m 755 "pqrs-${VER}-x86_64-unknown-linux-gnu/bin/pqrs" /usr/local/bin/pqrs \
    && rm -rf /tmp/pqrs*

# TOPCAT + STILTS — upstream discontinued topcat-extra.jar; the full jar now
# bundles the extra tools. Normalize wrapper scripts to whatever we install.
RUN TARBALL=topcat-full.jar \
    && curl -fsSL -o "/usr/local/bin/${TARBALL}" \
      "https://www.star.bris.ac.uk/~mbt/topcat/${TARBALL}" \
    && unzip -o "/usr/local/bin/${TARBALL}" -d /usr/local/bin topcat stilts \
    && sed -i -e 's/topcat-[a-z]*\.jar/topcat-full.jar/g' /usr/local/bin/topcat /usr/local/bin/stilts \
    && chmod 644 "/usr/local/bin/${TARBALL}" \
    && chmod 755 /usr/local/bin/stilts /usr/local/bin/topcat

# Science Python (uv) — one venv, one CUDA 12.9 toolkit.
# torch (cu129 index), tensorflow[and-cuda] (MaxiMask), and jax[cuda12]
# (scarlet2) are one transaction so a single nvidia-*-cu12 set is kept.
# Pin the cu129 local version. PyPI torch 2.13 is CUDA 13, and uv's
# first-index strategy stops on that wheel. unsafe-best-match lets the
# pin select cu129; that wheel's nvidia-*-cu12 pins are the only toolkit.
# ngmix is not on PyPI. scarlet + tractor have no usable wheels; build from git.
# Do not assert torch.cuda.is_available() here — builds have no GPU.
RUN apt-get update && apt-get install -y --no-install-recommends swig \
    && rm -rf /var/lib/apt/lists/*
COPY config/improc-py.txt /opt/astroai/improc-py.txt
COPY dockerfiles/improc/check-improc-python.py /opt/astroai/check-improc-python.py
RUN uv venv /opt/astroai/venv/improc \
    && uv pip install --python /opt/astroai/venv/improc \
         --index-strategy unsafe-best-match \
         --index-url https://download.pytorch.org/whl/cu129 \
         --extra-index-url https://pypi.org/simple \
         -r /opt/astroai/improc-py.txt \
         "torch==2.13.0+cu129" \
         "tensorflow[and-cuda]" \
         "jax[cuda12]" \
         torchfits \
         numba \
         "ngmix @ git+https://github.com/esheldon/ngmix" \
         numpy cython setuptools pybind11 peigen \
    && /opt/astroai/venv/improc/bin/python /opt/astroai/check-improc-python.py \
    && uv pip install --no-build-isolation --python /opt/astroai/venv/improc \
         "scarlet @ git+https://github.com/pmelchior/scarlet" \
    && uv pip install --no-build-isolation --python /opt/astroai/venv/improc \
         "tractor @ git+https://github.com/dstndstn/tractor" \
    && uv cache clean \
    && chmod -R a+rX /opt/astroai/venv/improc \
    && chmod a+r /opt/astroai/improc-py.txt /opt/astroai/check-improc-python.py \
    && for c in maximask maxitrack weightmask torchfits weightmask-reconstruct-sky; do \
         [[ -x "/opt/astroai/venv/improc/bin/${c}" ]] \
           && ln -sf "/opt/astroai/venv/improc/bin/${c}" "/usr/local/bin/${c}"; \
       done

# sourcextractor++ against this venv. The build tree is deleted in the same
# RUN. Boost 1.87 supplies Boost.Python for Python 3.13 (1.85 does not).
COPY dockerfiles/improc/build-sourcextractor.sh /tmp/build-sourcextractor.sh
RUN bash /tmp/build-sourcextractor.sh \
    && rm -f /tmp/build-sourcextractor.sh
COPY dockerfiles/improc/sourcextractor-wrapper.sh /usr/local/bin/sourcextractor++

COPY scripts/improc-profile.sh /etc/profile.d/improc.sh
COPY docs/USAGE.md /opt/astroai/USAGE.md
RUN chmod 755 /usr/local/bin/sourcextractor++ \
    && ln -sfn /usr/local/bin/sourcextractor++ /usr/local/bin/sourcextractor \
    && chmod a+r /etc/profile.d/improc.sh /opt/astroai/USAGE.md \
    && printf '%s\n' \
         '[ -f /etc/profile.d/improc.sh ] && . /etc/profile.d/improc.sh' \
         >> /etc/bash.bashrc \
    && rm -rf /usr/share/doc /usr/share/man /usr/share/info /usr/share/lintian /var/lib/apt/lists/* \
    && rm -rf /root/.cache /tmp/*

# Build-time smoke: required CLIs + Python imports
RUN set -euo pipefail; \
    . /etc/profile.d/improc.sh; \
    for c in \
      source-extractor sextractor swarp SWarp psfex scamp stiff missfits \
      fitscut fitspng fitsverify fpack funpack \
      weightwatcher montage theli \
      galfit imfit pqrs topcat stilts fits2idia imcore \
      astconvertt astnoisechisel astscript-psf-stamp \
      h5dump maximask maxitrack sky skymaker stuff torchfits weightmask; do \
        command -v "$c" >/dev/null || { echo "MISSING: $c" >&2; exit 1; }; \
    done; \
    command -v sourcextractor++ >/dev/null \
      || { echo "MISSING: sourcextractor++" >&2; exit 1; }; \
    command -v sourcextractor >/dev/null \
      || { echo "MISSING: sourcextractor" >&2; exit 1; }; \
    test ! -e /opt/astroai/conda; \
    sky -v >/dev/null; \
    timeout 180 sourcextractor++ --version >/dev/null; \
    /opt/astroai/venv/improc/bin/python /opt/astroai/check-improc-python.py; \
    /opt/astroai/venv/improc/bin/python -c "import ccdproc, photutils, galsim, piff, astroscrappy, lacosmic, sfft, zogyp, healpy, healsparse, astropy_healpix, mocpy, hpgeom, scarlet, scarlet2, twirl, skimage, astroquery, cv2, petrofit, montage_wrapper, galight, lenstronomy, tractor, torch, torchfits, tensorflow, maximask_and_maxitrack, weightmask, ngmix, sourcextractor; assert '+cu' in torch.__version__, torch.__version__"; \
    echo "improc smoke OK"
