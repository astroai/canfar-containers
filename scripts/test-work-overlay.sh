#!/bin/bash -e
# Prove astroai-lab WORK relocate for the CANFAR overlay case.
#
# Local docker tests usually bind-mount /srcdir, which is a different device
# from / — overlay_work_dir correctly leaves WORK=/srcdir. That path does not
# exercise CANFAR, where /srcdir is the container overlay (same device as /)
# and /scratch is a real volume. This script runs both layouts.
#
# Usage:
#   ./scripts/test-work-overlay.sh
#   IMAGE=images.canfar.net/astroai/terminal:local ./scripts/test-work-overlay.sh
set -o pipefail

REGISTRY="${REGISTRY:-images.canfar.net}"
OWNER="${OWNER:-astroai}"
TAG="${BUILD_TAG:-local}"
IMAGE="${IMAGE:-${REGISTRY}/${OWNER}/base:${TAG}}"
FAILURES=0

FAKE_ARC="$(mktemp -d)"
FAKE_SRC="$(mktemp -d)"
FAKE_SCRATCH="$(mktemp -d)"
trap 'rm -rf "${FAKE_ARC}" "${FAKE_SRC}" "${FAKE_SCRATCH}"' EXIT

mkdir -p "${FAKE_ARC}/testuser"
chmod -R a+rwX "${FAKE_ARC}" "${FAKE_SRC}" "${FAKE_SCRATCH}"

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    echo "Image missing: ${IMAGE} — run make build/base BUILD_TAG=${TAG}" >&2
    exit 1
fi

echo "WORK overlay relocate (${IMAGE})"
echo "================================"

check() {
    local label="$1"
    shift
    if "$@"; then
        printf '  ok  %s\n' "${label}"
    else
        printf '  FAIL %s\n' "${label}" >&2
        FAILURES=$((FAILURES + 1))
    fi
}

# Non-login bash so we can write /srcdir before the profile relocates WORK.
# Extra docker args (e.g. -e FLAG=0) go in front of the volume mounts.
overlay_probe() {
    rm -rf "${FAKE_ARC}/testuser"
    mkdir -p "${FAKE_ARC}/testuser"
    chmod -R a+rwX "${FAKE_ARC}"
    docker run --rm \
        -u "$(id -u):$(id -g)" \
        -e HOME="${FAKE_ARC}/testuser" \
        -e USER=testuser \
        -e SCRATCH=/scratch \
        "$@" \
        -v "${FAKE_ARC}/testuser:${FAKE_ARC}/testuser" \
        -v "${FAKE_SCRATCH}:/scratch" \
        "${IMAGE}" \
        bash -c '
set -e
export PATH="/opt/astroai/venv/cadc/bin:/opt/astroai/bin:${PATH}"
printf "seed\n" > /srcdir/from-overlay.txt
# shellcheck disable=SC1091
source /etc/profile.d/astroai.sh >/dev/null
printf "WORK=%s\n" "${WORK}"
if [[ -f /scratch/src/from-overlay.txt ]]; then
    printf "SEED=yes\n"
else
    printf "SEED=no\n"
fi
'
}

OUT="$(overlay_probe)"
check "overlay relocates WORK to /scratch/src" \
    grep -qx 'WORK=/scratch/src' <<<"${OUT}"
check "overlay seeds /srcdir into /scratch/src" \
    grep -qx 'SEED=yes' <<<"${OUT}"

OUT0="$(overlay_probe -e ASTROAI_LAB_WORK_ON_SCRATCH=0)"
check "ASTROAI_LAB_WORK_ON_SCRATCH=0 keeps WORK=/srcdir" \
    grep -qx 'WORK=/srcdir' <<<"${OUT0}"

rm -rf "${FAKE_ARC}/testuser"
mkdir -p "${FAKE_ARC}/testuser"
chmod -R a+rwX "${FAKE_ARC}"
BIND_OUT="$(docker run --rm \
    -u "$(id -u):$(id -g)" \
    -e HOME="${FAKE_ARC}/testuser" \
    -e USER=testuser \
    -e WORK=/srcdir \
    -e SCRATCH=/scratch \
    -v "${FAKE_ARC}/testuser:${FAKE_ARC}/testuser" \
    -v "${FAKE_SRC}:/srcdir" \
    -v "${FAKE_SCRATCH}:/scratch" \
    "${IMAGE}" \
    bash -c '
set -e
export PATH="/opt/astroai/venv/cadc/bin:/opt/astroai/bin:${PATH}"
# shellcheck disable=SC1091
source /etc/profile.d/astroai.sh >/dev/null
printf "WORK=%s\n" "${WORK}"
')"
check "bind-mounted /srcdir stays WORK=/srcdir" \
    grep -qx 'WORK=/srcdir' <<<"${BIND_OUT}"

if [[ "${FAILURES}" -eq 0 ]]; then
    echo "WORK overlay relocate passed."
    exit 0
fi
echo "${FAILURES} check(s) failed." >&2
exit 1
