#!/usr/bin/env bash
# Local smoke for the improc image family:
#   images.canfar.net/astroai/improc:<tag>
#   images.canfar.net/astroai/improc-webterm:<tag>   (ttyd shell on improc)
#   images.canfar.net/astroai/improc-notebook:<tag>  (JupyterLab on improc)
set -euo pipefail

TAG="${1:-${BUILD_TAG:-local}}"
REGISTRY="${REGISTRY:-images.canfar.net}"
OWNER="${OWNER:-astroai}"

check_image() {
    local name="$1"
    local extra="$2"  # image-specific check, e.g. "command -v ttyd" (empty for improc)
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
    /opt/astroai/venv/improc/bin/python -c \"import ccdproc, photutils, galsim, piff, astroscrappy, lacosmic, sfft, zogyp, healpy, healsparse, mocpy, hpgeom, scarlet, scarlet2, twirl, skimage, astroquery, cv2, petrofit, montage_wrapper, galight, lenstronomy, tractor, torch, torchfits, tensorflow, maximask_and_maxitrack, weightmask; assert '+cu' in torch.__version__, torch.__version__; print('PASS: improc python imports')\"
    /opt/astroai/conda/ngmix/bin/python -c \"import ngmix; print('PASS: ngmix')\"
    ${extra}
    exit \"\$missing\"
    "
    echo "smoke passed for ${image}"
}

check_image improc ""

# improc-webterm: browser shell on improc — ttyd must be present.
check_image improc-webterm "command -v ttyd >/dev/null && echo 'PASS: ttyd' || { echo 'FAIL: ttyd missing'; missing=\$((missing + 1)); }"

# improc-notebook: JupyterLab on improc — the improc science kernel must be registered.
check_image improc-notebook "jupyter kernelspec list 2>/dev/null | grep -q improc && echo 'PASS: improc jupyter kernel' || { echo 'FAIL: improc jupyter kernel not registered'; missing=\$((missing + 1)); }"

echo "improc family local smoke passed (improc, improc-webterm, improc-notebook)"
