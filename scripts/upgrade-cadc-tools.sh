#!/usr/bin/bash
# Upgrade packages in /opt/canfar (user-writable in AstroAI session images).
set -euo pipefail

CADC_VENV="/opt/canfar"
PY="${CADC_VENV}/bin/python"

usage() {
    cat <<'EOF'
Usage: upgrade-cadc-tools.sh [<uv pip install args>...]

Upgrade platform CADC/CANFAR Python tools in /opt/canfar.
Changes last for this session only — start a new session (or image tag) for
a clean slate; use a new image release for fleet-wide updates.

Examples:
  upgrade-cadc-tools.sh list
  upgrade-cadc-tools.sh --upgrade canfar-lab
  upgrade-cadc-tools.sh --upgrade canfar cadcdata cadctap vos
  upgrade-cadc-tools.sh --upgrade 'vosfs @ git+https://github.com/shinybrar/vosfs.git' 'vos-sync @ git+https://github.com/drtobybrown/vos-sync.git'

Build-time package list: `/opt/astroai/cadc-tools.txt` (unpinned; resolved when the image is built).

Note: uv, pixi, and micromamba are installed from upstream installers at image
build time (also unpinned). Project deps use pixi/uv under WORK; caches
and agent CLIs use scratch paths from canfar-lab env export.
EOF
}

if [[ ! -x "${PY}" ]]; then
    echo "error: ${CADC_VENV} not found" >&2
    exit 1
fi

if [[ ! -w "${CADC_VENV}" ]]; then
    echo "error: ${CADC_VENV} is not writable (need a current AstroAI base image)" >&2
    exit 1
fi

case "${1:-}" in
    -h | --help | help)
        usage
        exit 0
        ;;
    list)
        exec uv pip list --python "${CADC_VENV}"
        ;;
    "")
        usage
        exit 1
        ;;
esac

exec uv pip install --python "${CADC_VENV}" "$@"
