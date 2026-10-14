#!/bin/bash
# Local smoke for the specproc image family:
#   images.canfar.net/<owner>/specproc:<tag>
#   .../specproc-terminal:<tag>
#   .../specproc-notebook:<tag>
set -euo pipefail

TAG="${1:-local}"
OWNER="${OWNER:-astroai}"
REGISTRY="${REGISTRY:-images.canfar.net}"
PREFIX="${REGISTRY}/${OWNER}"

missing=0
check_image() {
    local name="$1"
    local extra="$2"
    echo "=== ${PREFIX}/${name}:${TAG} ==="
    docker run --rm "${PREFIX}/${name}:${TAG}" bash -lc "
        set -euo pipefail
        source /etc/profile.d/specproc.sh
        for c in julia babsma_lu bsyn_lu ferre; do
          command -v \"\$c\" >/dev/null || { echo \"MISSING: \$c\"; exit 1; }
        done
        test -s \"\${SPS_HOME}/data/emlines_info.dat\"
        test -L \"\${SPS_HOME}/SPECTRA\"
        test -L \"\${SPS_HOME}/ISOCHRONES\"
        test ! -d /opt/astroai/julia/depot/compiled
        grep -q Korg \"\${JULIA_PROJECT}/Project.toml\"
        python3 - <<'PY'
import pathlib
sp = next(pathlib.Path('/opt/astroai/venv/specproc/lib').glob('python*/site-packages'))
a = sp / 'synple' / 'linelists'
b = sp / 'synspec' / 'linelists'
assert a.is_symlink() and b.is_symlink(), (a, b)
assert a.readlink() == b.readlink() == pathlib.Path('/specproc-data/linelists/synspec')
PY
        HOME=/opt/astroai/pymoog-home /opt/astroai/venv/specproc/bin/python /opt/astroai/specproc-smoke.py
        ${extra}
    " || missing=$((missing + 1))
}

check_image specproc ""
check_image specproc-terminal "test -f /opt/ghostty-web/server.mjs && echo 'PASS: ghostty-web' || { echo 'FAIL: ghostty-web missing'; exit 1; }"
check_image specproc-notebook "jupyter kernelspec list 2>/dev/null | grep -q specproc && echo 'PASS: specproc jupyter kernel' || { echo 'FAIL: specproc jupyter kernel not registered'; exit 1; }"

if [[ "${missing}" -ne 0 ]]; then
    echo "${missing} specproc image(s) failed" >&2
    exit 1
fi
echo "specproc family local smoke passed (specproc, specproc-terminal, specproc-notebook)"
