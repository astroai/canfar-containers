# syntax=docker/dockerfile:1
# AstroAI specproc: instrument-blind spectroscopy on base.
# Group image. pPXF forbids redistribution and moogpy/pymoog vendor MOOG,
# so do not push this to the public astroai Harbor project.
# Headless. Julia + Fortran synthesizers; uv for the science venv.
# Versions float at build time — no lockfiles, no pin ARGs.

ARG REGISTRY=images.canfar.net
ARG OWNER=astroai
ARG TAG=local

FROM ${REGISTRY}/${OWNER}/base:${TAG}

LABEL org.opencontainers.image.title="AstroAI Specproc" \
      org.opencontainers.image.description="Group spectroscopy stack (synthesis, fitting, generic spectrum Python)" \
      io.canfar.skaha.session.type="headless"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Line lists, atmosphere grids, FSPS libraries, and filter catalogs are not
# baked. specproc-link-data.sh points every package at SPECPROC_DATA.
# JULIA_DEPOT_PATH stays the image depot until Korg is installed; a later
# ENV puts the precompile cache on a writable path.
ENV SPS_HOME=/opt/astroai/fsps \
    JULIA_DEPOT_PATH=/opt/astroai/julia/depot \
    JULIA_PROJECT=/opt/astroai/julia/korg \
    TURBOSPECTRUM_ROOT=/opt/astroai/Turbospectrum_NLTE \
    TSFITPY_ROOT=/opt/astroai/TSFitPy \
    SPECPROC_DATA=/specproc-data \
    ARIADNE_MODELS=/specproc-data/models/ariadne/spectra \
    ISOCHRONES=/specproc-data/isochrones \
    DUSTMAPS_CONFIG_FNAME=/opt/astroai/share/specproc/dustmapsrc

COPY scripts/specproc-link-data.sh /opt/astroai/specproc-link-data.sh
RUN chmod a+rx /opt/astroai/specproc-link-data.sh

# Julia 1.11. Korg lives in its own project so the depot is not a user home.
RUN ARCH="$(uname -m)" \
    && case "${ARCH}" in \
         x86_64) JARCH=x64; JMACH=x86_64 ;; \
         aarch64) JARCH=aarch64; JMACH=aarch64 ;; \
         *) echo "unsupported arch: ${ARCH}" >&2; exit 1 ;; \
       esac \
    && JULIA_VERSION=1.11.6 \
    && curl -fsSL -o /tmp/julia.tar.gz \
         "https://julialang-s3.julialang.org/bin/linux/${JARCH}/1.11/julia-${JULIA_VERSION}-linux-${JMACH}.tar.gz" \
    && mkdir -p /opt/astroai/julia \
    && tar -xzf /tmp/julia.tar.gz -C /opt/astroai/julia --strip-components=1 \
    && ln -sf /opt/astroai/julia/bin/julia /usr/local/bin/julia \
    && rm -f /tmp/julia.tar.gz \
    && mkdir -p "${JULIA_DEPOT_PATH}" "${JULIA_PROJECT}" \
    && JULIA_PKG_PRECOMPILE_AUTO=0 julia --project="${JULIA_PROJECT}" \
         -e 'using Pkg; Pkg.add("Korg")' \
    && bash /opt/astroai/specproc-link-data.sh \
    && chmod -R a+rX /opt/astroai/julia \
    && chmod -R a+rwX "${JULIA_DEPOT_PATH}" \
    && test ! -d "${JULIA_DEPOT_PATH}/compiled"

# FSPS code plus the small data/ index (emlines, filter tables). SPECTRA,
# ISOCHRONES, nebular, and dust are symlinks into SPECPROC_DATA.
RUN git clone --depth 1 --filter=blob:none --sparse \
      https://github.com/cconroy20/fsps.git /opt/astroai/fsps \
    && git -C /opt/astroai/fsps sparse-checkout set data src pro doc OUTPUTS \
    && test -s /opt/astroai/fsps/data/emlines_info.dat \
    && bash /opt/astroai/specproc-link-data.sh \
    && test -L /opt/astroai/fsps/SPECTRA \
    && test -L /opt/astroai/fsps/ISOCHRONES \
    && test ! -e /opt/astroai/fsps/.git \
    && chmod -R a+rX /opt/astroai/fsps

# One Turbospectrum NLTE build, shared by TSFitPy.
RUN git clone --depth 1 https://github.com/bertrandplez/Turbospectrum_NLTE.git \
      /opt/astroai/Turbospectrum_NLTE \
    && make -C /opt/astroai/Turbospectrum_NLTE/exec-gf -j1 \
    && install -m 755 /opt/astroai/Turbospectrum_NLTE/exec-gf/babsma_lu /usr/local/bin/babsma_lu \
    && install -m 755 /opt/astroai/Turbospectrum_NLTE/exec-gf/bsyn_lu /usr/local/bin/bsyn_lu \
    && git clone --depth 1 https://github.com/TSFitPy-developers/TSFitPy.git /opt/astroai/TSFitPy \
    && cp -a /opt/astroai/Turbospectrum_NLTE/interpolator/*.f \
         /opt/astroai/TSFitPy/scripts/model_interpolators/ \
    && ln -sfn /opt/astroai/Turbospectrum_NLTE /opt/astroai/TSFitPy/turbospectrum \
    && (cd /opt/astroai/TSFitPy/scripts && printf 'GNU\n' | python3 ./compile_fortran_codes.py) \
    && test -x /opt/astroai/TSFitPy/scripts/faltbon \
    && test -x /opt/astroai/TSFitPy/scripts/model_interpolators/interpol_modeles \
    && { grep -RIl 'compiler = ifort' /opt/astroai/TSFitPy \
           | while read -r cfg; do sed -i 's/compiler = ifort/compiler = gnu/' "$cfg"; done \
           || true; } \
    && bash /opt/astroai/specproc-link-data.sh \
    && test ! -e /opt/astroai/Turbospectrum_NLTE/.git \
    && test ! -e /opt/astroai/TSFitPy/.git \
    && chmod -R a+rX /opt/astroai/Turbospectrum_NLTE /opt/astroai/TSFitPy

# FERRE grid fitter. PyPI's ferre package is empty. Model grids stay on the mount.
RUN git clone --depth 1 https://github.com/callendeprieto/ferre.git /opt/astroai/ferre \
    && sed -i 's/^OPT= -O0$/OPT = -O2/' /opt/astroai/ferre/src/makefile \
    && make -C /opt/astroai/ferre/src -j1 \
    && test -x /opt/astroai/ferre/bin/ferre.x \
    && ln -sf /opt/astroai/ferre/bin/ferre.x /usr/local/bin/ferre \
    && bash /opt/astroai/specproc-link-data.sh \
    && test ! -e /opt/astroai/ferre/.git \
    && chmod -R a+rX /opt/astroai/ferre

COPY config/specproc-py.txt /opt/astroai/specproc-py.txt
COPY scripts/specproc-patch-bindir.py /opt/astroai/specproc-patch-bindir.py

# Wheels first. PyPI sedpy is an unrelated editor; the astronomy package is astro-sedpy.
RUN uv venv /opt/astroai/venv/specproc \
    && uv pip install --python /opt/astroai/venv/specproc -r /opt/astroai/specproc-py.txt \
    && bash /opt/astroai/specproc-link-data.sh

# Fortran wrappers. Their setup.py compiles only when sys.argv contains
# install or bdist_wheel, which a PEP 517 wheel build does not set. Run
# setup.py install so the binaries land in the venv bin.
# Nidever babsma_lu/bsyn_lu then sit ahead of the NLTE copies in
# /usr/local/bin. TSFitPy uses TURBOSPECTRUM_ROOT, not PATH.
# The PyPI synple sdist omits the Fortran tree; install that one from git.
# moogpy vendors MOOG. pymoog is the maintained MOOG automation (the 2009
# autoMOOG program has no public source release) and stores files in HOME.
RUN set -euo pipefail \
    && PY=/opt/astroai/venv/specproc/bin/python \
    && export PATH="/opt/astroai/venv/specproc/bin:${PATH}" \
    && uv pip install --python "${PY}" pip setuptools wheel setuptools_scm \
    && printf '%s\n' '#!/bin/sh' 'exec /usr/bin/wget -q "$@"' \
         > /opt/astroai/venv/specproc/bin/wget \
    && chmod 755 /opt/astroai/venv/specproc/bin/wget \
    && mkdir -p /tmp/src \
    && "${PY}" -m pip download --no-deps --no-binary :all: -d /tmp/src turbospectrum synspec \
    && for pkg in turbospectrum synspec; do \
         rm -rf "/tmp/src/${pkg}"; \
         mkdir -p "/tmp/src/${pkg}/bin"; \
         tar -xzf /tmp/src/${pkg}-*.tar.gz -C "/tmp/src/${pkg}" --strip-components=1; \
         "${PY}" /opt/astroai/specproc-patch-bindir.py "/tmp/src/${pkg}/setup.py"; \
         if [[ "${pkg}" == synspec ]]; then \
           mkdir -p /opt/astroai/share/specproc; \
           cp /tmp/src/synspec/python/synspec/linelists/makefile \
             /opt/astroai/share/specproc/synspec-linelists.mk; \
           printf 'all:\n\ttrue\n' > /tmp/src/synspec/python/synspec/linelists/makefile; \
         fi; \
         (cd "/tmp/src/${pkg}" && "${PY}" setup.py install); \
       done \
    && test -x /opt/astroai/venv/specproc/bin/babsma_lu \
    && test -x /opt/astroai/venv/specproc/bin/bsyn_lu \
    && test -x /opt/astroai/venv/specproc/bin/synspec54 \
    && bash /opt/astroai/specproc-link-data.sh \
    && rm -rf /tmp/src

# synple's package data paths are symlinks. setuptools refuses to copy those.
RUN set -euo pipefail \
    && PY=/opt/astroai/venv/specproc/bin/python \
    && export PATH="/opt/astroai/venv/specproc/bin:${PATH}" \
    && git clone --depth 1 https://github.com/dnidever/synple.git /tmp/src/synple \
    && rm -f /tmp/src/synple/python/synple/data /tmp/src/synple/python/synple/linelists \
    && mv /tmp/src/synple/data /tmp/src/synple/python/synple/data \
    && mv /tmp/src/synple/linelists /tmp/src/synple/python/synple/linelists \
    && ln -s python/synple/data /tmp/src/synple/data \
    && ln -s python/synple/linelists /tmp/src/synple/linelists \
    && "${PY}" /opt/astroai/specproc-patch-bindir.py /tmp/src/synple/setup.py \
    && printf 'all:\n\ttrue\n' > /tmp/src/synple/python/synple/linelists/makefile \
    && (cd /tmp/src/synple && "${PY}" setup.py install) \
    && bash /opt/astroai/specproc-link-data.sh \
    && rm -rf /tmp/src/synple \
    && test -L /opt/astroai/venv/specproc/lib/python*/site-packages/synple/linelists \
    && test -L /opt/astroai/venv/specproc/lib/python*/site-packages/synspec/linelists

COPY scripts/scipy-array-api-compat-shim.py /opt/astroai/scipy-array-api-compat-shim.py
RUN set -euo pipefail \
    && PY=/opt/astroai/venv/specproc/bin/python \
    && export PATH="/opt/astroai/venv/specproc/bin:${PATH}" \
    && git clone --depth 1 https://github.com/dnidever/moogpy.git /tmp/src/moogpy \
    && "${PY}" /opt/astroai/specproc-patch-bindir.py /tmp/src/moogpy/setup.py \
    && sed -i \
         -e 's/-lX11//g' \
         -e 's/-lplotsub//g' \
         -e 's/-ldevices//g' \
         -e 's/-lutils//g' \
         -e 's/-L$(X11LIB)//g' \
         -e 's/-L$(SMLIB)//g' \
         -e 's/^FC = f77 -Wall/FC = gfortran -fno-range-check -w -fallow-argument-mismatch -ff2c/' \
         -e 's/Binplotprep\.o Blends\.o/Binplotprep.o Blankstring.o Blends.o/' \
         -e 's/Getnum\.o \\/Getnum.o Getsyns.o \\/' \
         /tmp/src/moogpy/src/Makefile.rh64 /tmp/src/moogpy/src/Makefile.rh64silent \
    && (cd /tmp/src/moogpy && "${PY}" setup.py install) \
    && test -x /opt/astroai/venv/specproc/bin/MOOG \
    && test -x /opt/astroai/venv/specproc/bin/MOOGSILENT \
    && uv pip install --python "${PY}" --no-build-isolation \
         "theborg @ git+https://github.com/dnidever/theborg" \
         "fraunhofer @ git+https://github.com/dnidever/fraunhofer" \
         "roland @ git+https://github.com/dnidever/roland" \
         "starlyte @ git+https://github.com/dnidever/starlyte" \
    && mkdir -p /opt/astroai/pymoog-home \
    && HOME=/opt/astroai/pymoog-home "${PY}" -m pip install --no-cache-dir pymoog \
    && test -x /opt/astroai/pymoog-home/.pymoog/moog_nosm/moog_nosm_NOV2019/MOOG \
    && uv pip install --python "${PY}" array-api-compat \
    && dest="$("${PY}" -c 'import pathlib, scipy; print(pathlib.Path(scipy.__file__).parent / "_lib" / "array_api_compat.py")')" \
    && cp /opt/astroai/scipy-array-api-compat-shim.py "${dest}" \
    && rm -f /opt/astroai/venv/specproc/bin/wget \
    && bash /opt/astroai/specproc-link-data.sh \
    && if compgen -G "/opt/astroai/pymoog-home/.pymoog/files/pymoog_lf_*.tar.gz" > /dev/null; then \
         echo "pymoog line-list tarball still in the image" >&2; exit 1; \
       fi \
    && test -L /opt/astroai/pymoog-home/.pymoog/files/pymoog_lf/linelist \
    && test -L /opt/astroai/pymoog-home/.pymoog/files/pymoog_lf/model \
    && uv cache clean \
    && rm -rf /tmp/src \
    && chmod -R a+rX /opt/astroai/venv/specproc /opt/astroai/pymoog-home

ENV PATH="/opt/astroai/venv/specproc/bin:/opt/astroai/julia/bin:${PATH}"

COPY scripts/specproc-profile.sh /etc/profile.d/specproc.sh
RUN chmod a+r /etc/profile.d/specproc.sh \
    && printf '%s\n' \
         '[ -f /etc/profile.d/specproc.sh ] && . /etc/profile.d/specproc.sh' \
         >> /etc/bash.bashrc \
    && rm -rf /root/.cache /tmp/* \
    && chmod -R a+rX /opt/astroai/venv/specproc

# pyKurucz is the ATLAS12+SYNTHE loop (MIT, pure Python). astroARIADNE averages
# photometric SEDs across PHOENIX, BT-Settl, and Kurucz. iNNterpol interpolates
# ATLAS9, MARCS, and PHOENIX structures. Atmosphere grids, dust maps, filters,
# and the Kurucz line lists stay on the mount. The emulator weights are small
# and are pulled; GFALL is a symlink into SPECPROC_DATA. pyrre and CPU torch
# are not installed.
ENV PYTHONPATH=/opt/astroai/pykurucz
RUN set -euo pipefail \
    && PY=/opt/astroai/venv/specproc/bin/python \
    && uv pip install --python "${PY}" astroARIADNE \
    && git lfs install \
    && GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 \
         https://github.com/tingyuansen/pykurucz.git /opt/astroai/pykurucz \
    && git -C /opt/astroai/pykurucz lfs pull --include="emulator/a_one_weights.pt" \
    && test "$(stat -c%s /opt/astroai/pykurucz/emulator/a_one_weights.pt)" -gt 1000000 \
    && test "$(stat -c%s /opt/astroai/pykurucz/lines/gfallvac.latest)" -lt 1000000 \
    && git clone --depth 1 https://github.com/cwestend/iNNterpol.git /opt/astroai/iNNterpol \
    && bash /opt/astroai/specproc-link-data.sh \
    && test -L /opt/astroai/pykurucz/lines/gfallvac.latest \
    && test ! -e /opt/astroai/pykurucz/.git \
    && test ! -e /opt/astroai/iNNterpol/.git \
    && uv cache clean \
    && chmod -R a+rX /opt/astroai/pykurucz /opt/astroai/iNNterpol /opt/astroai/venv/specproc

# Agama compiles its C++ library. VICE 1.3.1 still casts with Cython's
# builtin `long`, which Cython 3.1 removed, so the build uses Cython 3.0
# and then drops that compiler pin.
RUN set -euo pipefail \
    && PY=/opt/astroai/venv/specproc/bin/python \
    && "${PY}" -m pip install --no-cache-dir -q --no-build-isolation \
         --config-settings="--build-option=--yes" \
         "agama @ git+https://github.com/GalacticDynamics-Oxford/Agama.git" \
    && "${PY}" -m pip install --no-cache-dir -q "cython>=3,<3.1" \
    && "${PY}" -m pip install --no-cache-dir -q --no-build-isolation vice \
    && "${PY}" -m pip uninstall -y cython \
    && rm -rf /root/.cache /tmp/* \
    && chmod -R a+rX /opt/astroai/venv/specproc

# Precompile cache is not part of the image. The first `using Korg` writes it
# to the first entry of JULIA_DEPOT_PATH (scratch in a session, /tmp here).
ENV JULIA_DEPOT_PATH=/tmp/julia-compile:/opt/astroai/julia/depot

# Build-time smoke. Korg opens the atmosphere HDF5 files while loading, so
# `using Korg` waits until those files are on the mount.
COPY scripts/specproc-smoke.py /opt/astroai/specproc-smoke.py
RUN chmod a+r /opt/astroai/specproc-smoke.py \
    && set -euo pipefail \
    && for c in julia babsma_lu bsyn_lu ferre; do \
         command -v "$c" >/dev/null || { echo "MISSING: $c" >&2; exit 1; }; \
       done \
    && test -s "${SPS_HOME}/data/emlines_info.dat" \
    && test -L "${SPS_HOME}/SPECTRA" \
    && test -L "${SPS_HOME}/ISOCHRONES" \
    && grep -q Korg "${JULIA_PROJECT}/Project.toml" \
    && find /opt/astroai/julia/depot/artifacts -name SDSS_MARCS_atmospheres.h5 -type l | grep -q . \
    && test ! -d /opt/astroai/julia/depot/compiled \
    && HOME=/opt/astroai/pymoog-home /opt/astroai/venv/specproc/bin/python /opt/astroai/specproc-smoke.py \
    && rm -rf /opt/astroai/pymoog-home/.sme /opt/astroai/pymoog-home/.cache /root/.sme /root/.cache
