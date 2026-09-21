#!/bin/bash -e
# Local smoke test: run an AstroAI session image as a non-root user.
#
# Bind-mounts host dirs at /srcdir and /scratch. That is the volume layout
# (WORK stays /srcdir). The CANFAR overlay path — /srcdir on the container
# overlay, WORK → /scratch/src — is covered by test-work-overlay.sh.
#
# Usage:
#   ./scripts/test-local.sh terminal [port]
#   ./scripts/test-local.sh notebook [port]   # defaults to 8888
#   ./scripts/test-local.sh terminal --verify-only   # PATH/CADC checks only (no server)

IMAGE="${1:-terminal}"
PORT="${2:-}"
VERIFY_ONLY=0

if [[ "${IMAGE}" == "--verify-only" ]]; then
    VERIFY_ONLY=1
    IMAGE="${2:-terminal}"
    PORT="${3:-}"
elif [[ "${PORT}" == "--verify-only" || "${2:-}" == "--verify-only" ]]; then
    VERIFY_ONLY=1
    PORT="${3:-}"
fi

OWNER="${OWNER:-astroai}"
REGISTRY="${REGISTRY:-images.canfar.net}"
# Prefer TAG; fall back to BUILD_TAG so release smokes match bake/push.
TAG="${TAG:-${BUILD_TAG:-local}}"
SESSION_ID="${SESSION_ID:-test-session-001}"
FAILURES=0

FAKE_ARC="$(mktemp -d)"
FAKE_SRC="$(mktemp -d)"
FAKE_SCRATCH="$(mktemp -d)"
trap 'rm -rf "${FAKE_ARC}" "${FAKE_SRC}" "${FAKE_SCRATCH}"' EXIT

mkdir -p "${FAKE_ARC}/testuser"
FULL_IMAGE="${REGISTRY}/${OWNER}/${IMAGE}:${TAG}"

run_docker() {
    docker run --rm \
        -u "$(id -u):$(id -g)" \
        -e HOME="${FAKE_ARC}/testuser" \
        -e USER=testuser \
        "${EXTRA_ENV[@]}" \
        -v "${FAKE_ARC}/testuser:${FAKE_ARC}/testuser" \
        -v "${FAKE_SRC}:/srcdir" \
        -v "${FAKE_SCRATCH}:/scratch" \
        "${FULL_IMAGE}" \
        "$@"
}

if [[ "${IMAGE}" == "notebook" ]]; then
    PORT="${PORT:-8888}"
    CONTAINER_PORT=8888
    EXTRA_ENV=(-e "JUPYTER_TOKEN=${SESSION_ID}")
    RUN_CMD=(/skaha/startup.sh "${SESSION_ID}")
    ACCESS_URL="http://127.0.0.1:${PORT}/session/notebook/${SESSION_ID}/"
else
    PORT="${PORT:-5000}"
    CONTAINER_PORT=5000
    EXTRA_ENV=(-e "skaha_sessionid=${SESSION_ID}")
    RUN_CMD=(/skaha/startup.sh)
    # Contributed ingress strips /session/contrib/<id>; container serves at /
    ACCESS_URL="http://127.0.0.1:${PORT}/"
fi

if [[ "${VERIFY_ONLY}" -eq 1 ]]; then
    echo "Verifying ${FULL_IMAGE} (quick smoke: PATH + CADC CLIs + tools)"
    echo "  HOME=${FAKE_ARC}/testuser"
    echo "  /srcdir=${FAKE_SRC}  /scratch=${FAKE_SCRATCH}"
    echo ""

    # --quick: PATH, CADC CLIs, astroai status/env export/save --list/agent, core tools,
    #          writable venv, peek.  Skips interactive-shell probes, canfar API
    #          calls, cache-dir layout, and agent installs.
    run_docker /opt/astroai/bin/canfar-verify.sh --quick || FAILURES=$((FAILURES + 1))
    exit ${FAILURES}
fi

echo "Running ${FULL_IMAGE} on ${ACCESS_URL}"
echo "  HOME=${FAKE_ARC}/testuser"
echo "  /srcdir=${FAKE_SRC}  /scratch=${FAKE_SCRATCH}"
echo "  session=${SESSION_ID}"

TTY_ARGS=()
if [[ -t 0 ]]; then
    TTY_ARGS=(-it)
fi

docker run --rm "${TTY_ARGS[@]}" \
    -u "$(id -u):$(id -g)" \
    -e HOME="${FAKE_ARC}/testuser" \
    -e USER=testuser \
    "${EXTRA_ENV[@]}" \
    -v "${FAKE_ARC}/testuser:${FAKE_ARC}/testuser" \
    -v "${FAKE_SRC}:/srcdir" \
    -v "${FAKE_SCRATCH}:/scratch" \
    -p "${PORT}:${CONTAINER_PORT}" \
    "${FULL_IMAGE}" \
    "${RUN_CMD[@]}" || FAILURES=$((FAILURES + 1))

exit ${FAILURES}
