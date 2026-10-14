#!/bin/bash
# Build Elements 6.3.7, Alexandria 2.33.0, and SourceXtractor++ 1.1.0 against
# the improc virtualenv (Python 3.13). InstallArea RPATHs are absolute, so the
# tree stays at /opt/astroai/astrorama. Headers, static libs, and build trees
# are removed before this script exits.
set -euo pipefail

PY=/opt/astroai/venv/improc/bin/python
ROOT=/opt/astroai/astrorama
export PATH="/opt/astroai/venv/improc/bin:${PATH}"
export DEBIAN_FRONTEND=noninteractive

# Template-heavy C++ exhausts a 16 GB builder above two compile jobs.
JOBS="$(nproc)"
if [[ "$JOBS" -gt 2 ]]; then
    JOBS=2
fi
export CMAKE_BUILD_PARALLEL_LEVEL="$JOBS"
export MAKEFLAGS="-j${JOBS}"

DEV_PKGS=(
    libcfitsio-dev
    libccfits-dev
    wcslib-dev
    libfftw3-dev
    libgsl-dev
    liblog4cpp5-dev
    libeigen3-dev
    liblapack-dev
    libblas-dev
    libncurses-dev
    libreadline-dev
    libre2-dev
    zlib1g-dev
    libbz2-dev
)
# Kept after the -dev packages are purged. sourcextractor++ needs the FFTW
# single-precision library as well as the double one.
RUNTIME_PKGS=(
    libfftw3-double3
    libfftw3-single3
    libgsl28
    libgslcblas0
    libwcs8
    liblog4cpp5v5
)

apt-get update
apt-get install -y --no-install-recommends "${DEV_PKGS[@]}" "${RUNTIME_PKGS[@]}"

echo "=== ONNX Runtime 1.26 CPU ==="
curl -fsSL -o /tmp/onnx.tgz \
    https://github.com/microsoft/onnxruntime/releases/download/v1.26.0/onnxruntime-linux-x64-1.26.0.tgz
rm -rf /tmp/onnx
mkdir -p /tmp/onnx
tar -xzf /tmp/onnx.tgz -C /tmp/onnx
onnx_src="$(find /tmp/onnx -mindepth 1 -maxdepth 1 -type d | head -1)"
rm -rf /usr/local/onnxruntime
mkdir -p /usr/local/onnxruntime
cp -a "${onnx_src}/include" /usr/local/onnxruntime/include
cp -a "${onnx_src}/lib" /usr/local/onnxruntime/lib
rm -rf /tmp/onnx /tmp/onnx.tgz
test -f /usr/local/onnxruntime/include/onnxruntime_cxx_api.h
test -e /usr/local/onnxruntime/lib/libonnxruntime.so

echo "=== levmar 2.6 ==="
curl -fsSL -o /tmp/levmar.tgz \
    https://mirrors.dotsrc.org/mirrors/pub/pub/mirrors/gentoo/distfiles/a9/levmar-2.6.tgz
rm -rf /tmp/levmar-2.6
tar -xzf /tmp/levmar.tgz -C /tmp
make -C /tmp/levmar-2.6 -j"$JOBS" \
    CFLAGS="-O2 -fPIC" \
    LAPACKLIBS="-llapack -lblas" \
    liblevmar.a
gcc -shared -fPIC -o /usr/local/lib/liblevmar.so.2.6 \
    -Wl,-soname,liblevmar.so.2.6 \
    /tmp/levmar-2.6/lm.o /tmp/levmar-2.6/Axb.o /tmp/levmar-2.6/misc.o \
    /tmp/levmar-2.6/lmlec.o /tmp/levmar-2.6/lmbc.o /tmp/levmar-2.6/lmblec.o \
    /tmp/levmar-2.6/lmbleic.o \
    -llapack -lblas -lm
ln -sfn liblevmar.so.2.6 /usr/local/lib/liblevmar.so
install -m 644 /tmp/levmar-2.6/levmar.h /tmp/levmar-2.6/lm.h /usr/local/include/
rm -rf /tmp/levmar-2.6 /tmp/levmar.tgz

# Boost 1.85 (the astrorama conda build) has no Python 3.13 Boost.Python.
# 1.87 is the first stable release that does. Shared libraries only; Debian's
# libboost-python is tied to Debian's interpreter, not this image's Python.
echo "=== Boost 1.87 ==="
"$PY" -c 'import numpy; print("numpy", numpy.__version__, numpy.get_include())'
curl -fsSL -o /tmp/boost.tar.gz \
    https://archives.boost.io/release/1.87.0/source/boost_1_87_0.tar.gz
rm -rf /tmp/boost_1_87_0
tar -xzf /tmp/boost.tar.gz -C /tmp
cd /tmp/boost_1_87_0
./bootstrap.sh --prefix=/usr/local --with-python="$PY"
cat > project-config.jam << EOF
import option ;
using gcc ;
using python : 3.13 : ${PY} : /usr/local/include/python3.13 : /usr/local/lib ;
option.set prefix : /usr/local ;
option.set exec-prefix : /usr/local ;
option.set libdir : /usr/local/lib ;
option.set includedir : /usr/local/include ;
EOF
# Header install prints one line per file; keep that out of the build log.
if ! ./b2 -d0 -j"$JOBS" --layout=system \
    link=shared runtime-link=shared threading=multi variant=release \
    --with-python --with-filesystem --with-thread --with-program_options \
    --with-regex --with-atomic --with-chrono --with-date_time --with-timer \
    --with-test --with-iostreams --with-serialization \
    install > /tmp/b2.log 2>&1; then
    tail -80 /tmp/b2.log >&2
    exit 1
fi
rm -f /tmp/b2.log
echo "Boost 1.87 shared libraries installed"
cd /
rm -rf /tmp/boost_1_87_0 /tmp/boost.tar.gz
test -e /usr/local/lib/libboost_python313.so
test -e /usr/local/lib/libboost_numpy313.so
test -e /usr/local/lib/libboost_filesystem.so
test -e /usr/local/lib/libboost_timer.so
test -e /usr/local/lib/libboost_unit_test_framework.so
test -e /usr/local/lib/libboost_iostreams.so
test -e /usr/local/lib/libboost_serialization.so
ldconfig

echo "=== Elements / Alexandria / SourceXtractor++ ==="
rm -rf "$ROOT"
mkdir -p "$ROOT"
git clone --depth 1 --branch 6.3.7 https://github.com/astrorama/Elements.git \
    "$ROOT/Elements"
git clone --depth 1 --branch 2.33.0 https://github.com/astrorama/Alexandria.git \
    "$ROOT/Alexandria"
git clone --depth 1 --branch 1.1.0 \
    https://github.com/astrorama/SourceXtractorPlusPlus.git \
    "$ROOT/SourceXtractorPlusPlus"

export CMAKE_PROJECT_PATH="$ROOT"
export CMAKE_PREFIX_PATH="${ROOT}/Elements/cmake:/usr/local"
export BOOST_ROOT=/usr/local
export ONNXRUNTIME_INSTALL_DIR=/usr/local/onnxruntime
export LEVMAR_ROOT_DIR=/usr/local
export CMAKEFLAGS="-DPYTHON_EXPLICIT_VERSION=3.13 -DUSE_SPHINX=OFF -DELEMENTS_BUILD_TESTS=OFF -DPYTHON_EXECUTABLE=${PY} -DPython_EXECUTABLE=${PY} -DPython3_EXECUTABLE=${PY} -DPYTHON_LIBRARY=/usr/local/lib/libpython3.13.so -DPYTHON_INCLUDE_DIR=/usr/local/include/python3.13 -DBOOST_ROOT=/usr/local -DONNXRUNTIME_INSTALL_DIR=/usr/local/onnxruntime -DLEVMAR_ROOT_DIR=/usr/local"

for proj in Elements Alexandria SourceXtractorPlusPlus; do
    echo "=== building ${proj} ==="
    if ! make -C "$ROOT/$proj" install > "/tmp/${proj}.log" 2>&1; then
        tail -120 "/tmp/${proj}.log" >&2
        exit 1
    fi
    echo "=== ${proj} installed ==="
done

if grep -F "Levmar library not found" /tmp/*.log; then
    echo "model fitting was built without levmar" >&2
    exit 1
fi
if grep -F "ONNX Runtime not found" /tmp/*.log; then
    echo "segmentation was built without ONNX Runtime" >&2
    exit 1
fi
if grep -F "without model fitting" /tmp/*.log; then
    echo "model fitting was dropped" >&2
    exit 1
fi
rm -f /tmp/Elements.log /tmp/Alexandria.log /tmp/SourceXtractorPlusPlus.log

# Drop sources, headers, and static archives. Keep InstallArea bin/lib/python.
for proj in Elements Alexandria SourceXtractorPlusPlus; do
    find "$ROOT/$proj" -mindepth 1 -maxdepth 1 ! -name InstallArea -exec rm -rf {} +
    find "$ROOT/$proj/InstallArea" -type d \
        \( -name include -o -name cmake -o -name doc \) -prune -exec rm -rf {} +
    find "$ROOT/$proj/InstallArea" -type f \( -name '*.a' -o -name '*.o' \) -delete
done
rm -rf /usr/local/include/boost /usr/local/lib/cmake/Boost* /usr/local/lib/cmake/boost*
find /usr/local/lib -name 'libboost_*.a' -delete
rm -rf /usr/local/onnxruntime/include
rm -f /usr/local/include/levmar.h /usr/local/include/lm.h

find "$ROOT" /usr/local/onnxruntime/lib -type f \
    \( -name '*.so' -o -name '*.so.*' \) -exec strip --strip-unneeded {} + || true
find /usr/local/lib \( -name 'libboost_*.so*' -o -name 'liblevmar.so*' \) \
    -exec strip --strip-unneeded {} + || true

site="$("$PY" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
: > "${site}/astrorama.pth"
shopt -s nullglob
for py in "$ROOT"/*/InstallArea/*/python; do
    if [[ -e "$py/numpy" || -e "$py/astropy" ]]; then
        echo "refusing to copy a second scientific stack from ${py}" >&2
        exit 1
    fi
    cp -a "$py"/. "$site"/
    echo "$py" >> "${site}/astrorama.pth"
done
shopt -u nullglob
chmod a+r "${site}/astrorama.pth"

{
    find "$ROOT" -type d -path '*/InstallArea/*/lib'
    echo /usr/local/onnxruntime/lib
} > /etc/ld.so.conf.d/astrorama.conf
ldconfig

shopt -s nullglob
bins=( "$ROOT"/SourceXtractorPlusPlus/InstallArea/*/bin/sourcextractor++ )
shopt -u nullglob
if [[ ${#bins[@]} -ne 1 || ! -x "${bins[0]}" ]]; then
    echo "expected one sourcextractor++ binary, found: ${bins[*]:-none}" >&2
    find "$ROOT" -type f -name 'sourcextractor*' >&2 || true
    exit 1
fi
if ldd "${bins[0]}" | grep -q 'not found'; then
    ldd "${bins[0]}" >&2
    exit 1
fi
for needle in libonnxruntime liblevmar libgsl libfftw3f libboost_python; do
    if ! ldd "${bins[0]}" | grep -q "$needle"; then
        echo "sourcextractor++ does not link ${needle}" >&2
        ldd "${bins[0]}" >&2
        exit 1
    fi
done
mapfile -t pysos < <(find "$site" "$ROOT" -name '_SourceXtractorPy*.so')
if [[ ${#pysos[@]} -eq 0 ]]; then
    echo "_SourceXtractorPy extension not installed" >&2
    find "$ROOT" "$site" -name '*.so' -o -name '*.py' | head -50 >&2 || true
    exit 1
fi
for so in "${pysos[@]}"; do
    if ldd "$so" | grep -q 'not found'; then
        echo "unresolved libraries in ${so}" >&2
        ldd "$so" >&2
        exit 1
    fi
done

chmod -R a+rX "$ROOT" /usr/local/onnxruntime

# Purge headers. Do not autoremove: the runtime libraries above must stay.
apt-get purge -y "${DEV_PKGS[@]}"
rm -rf /var/lib/apt/lists/*
test ! -e /opt/astroai/conda
echo "sourcextractor build OK: ${bins[0]}"
