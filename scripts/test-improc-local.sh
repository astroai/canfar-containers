#!/usr/bin/env bash
# Local smoke for the improc image family:
#   images.canfar.net/astroai/improc:<tag>
#   images.canfar.net/astroai/improc-terminal:<tag>   (browser shell on improc)
#   images.canfar.net/astroai/improc-notebook:<tag>  (JupyterLab on improc)
set -euo pipefail

TAG="${1:-${BUILD_TAG:-local}}"
REGISTRY="${REGISTRY:-images.canfar.net}"
OWNER="${OWNER:-astroai}"

check_image() {
    local name="$1"
    local extra="$2"  # image-specific check (empty for improc)
    local image="${REGISTRY}/${OWNER}/${name}:${TAG}"
    echo "Testing ${image}"
    docker run --rm --entrypoint bash "${image}" -lc "
    source /etc/profile.d/astroai.sh 2>/dev/null || true
    source /etc/profile.d/improc.sh
    set -euo pipefail
    missing=0
    for c in \\
      source-extractor sextractor swarp psfex scamp stiff missfits \\
      fitscut fitspng fitsverify weightwatcher montage \\
      galfit imfit pqrs topcat stilts fits2idia imcore \\
      astconvertt astnoisechisel h5dump maximask maxitrack sky skymaker stuff torchfits weightmask; do
      if ! command -v \"\$c\" >/dev/null; then
        echo \"FAIL: missing \$c\"
        missing=\$((missing + 1))
      else
        echo \"PASS: \$c -> \$(command -v \"\$c\")\"
      fi
    done
    if ! command -v sourcextractor++ >/dev/null && ! command -v sourcextractor >/dev/null; then
      echo \"FAIL: sourcextractor++\"
      missing=\$((missing + 1))
    else
      echo \"PASS: sourcextractor++\"
    fi
    if [[ -e /opt/astroai/conda ]]; then
      echo \"FAIL: /opt/astroai/conda\"
      missing=\$((missing + 1))
    fi
    if ! sky -v >/dev/null; then
      echo \"FAIL: sky -v\"
      missing=\$((missing + 1))
    else
      echo \"PASS: sky -v\"
    fi
    if ! timeout 180 sourcextractor++ --version >/dev/null; then
      echo \"FAIL: sourcextractor++ --version\"
      missing=\$((missing + 1))
    else
      echo \"PASS: sourcextractor++ --version\"
    fi
    /opt/astroai/venv/improc/bin/python /opt/astroai/check-improc-python.py
    /opt/astroai/venv/improc/bin/python -c \"import ccdproc, photutils, galsim, piff, astroscrappy, lacosmic, sfft, zogyp, healpy, healsparse, mocpy, hpgeom, scarlet, scarlet2, twirl, skimage, astroquery, cv2, petrofit, montage_wrapper, galight, lenstronomy, tractor, torch, torchfits, tensorflow, maximask_and_maxitrack, weightmask, ngmix, sourcextractor; assert '+cu' in torch.__version__, torch.__version__; print('PASS: improc python imports')\"
    ${extra}
    exit \"\$missing\"
    "
    echo "smoke passed for ${image}"
}

check_image improc ""

# improc-terminal: browser shell on improc — ghostty-web server must be present.
check_image improc-terminal "test -f /opt/ghostty-web/server.mjs && echo 'PASS: ghostty-web' || { echo 'FAIL: ghostty-web missing'; missing=\$((missing + 1)); }"

# improc-notebook: JupyterLab on improc — the improc science kernel must be registered.
check_image improc-notebook "jupyter kernelspec list 2>/dev/null | grep -q improc && echo 'PASS: improc jupyter kernel' || { echo 'FAIL: improc jupyter kernel not registered'; missing=\$((missing + 1)); }"

echo "improc family local smoke passed (improc, improc-terminal, improc-notebook)"
