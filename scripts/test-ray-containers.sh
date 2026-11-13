#!/bin/bash -e
# Ray image layout checks.
#
# Usage:
#   test-ray-containers.sh            full image checks
#   test-ray-containers.sh --smoke     fast smoke (core checks only)

set -o pipefail

REGISTRY="${REGISTRY:-images.canfar.net}"
OWNER="${OWNER:-astroai}"
TAG="${BUILD_TAG:-local}"
FAILURES=0
SMOKE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --smoke) SMOKE=1; shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

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

MGR="${REGISTRY}/${OWNER}/ray-manager:${TAG}"
WRK="${REGISTRY}/${OWNER}/ray-worker:${TAG}"

echo "Ray container verification"
[[ "${SMOKE}" -eq 1 ]] && echo "(smoke mode — core checks only)"
echo "=========================="

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

check "worker has no ASTROAI_LAB_RESUME hook" \
    bash -c "! grep -q ASTROAI_LAB_RESUME '${ROOT}/ray/worker/start-worker.sh'"

check "ray-manager startup.sh" docker run --rm --entrypoint test "${MGR}" -x /skaha/startup.sh
check "ray-worker entrypoint" docker run --rm --entrypoint test "${WRK}" -x /opt/astroai/bin/start-ray-worker.sh
check "ray installed" docker run --rm --entrypoint python "${WRK}" -c "import ray; print(ray.__version__)"
check "shared image python (worker)" docker run --rm --entrypoint assert-image-python "${WRK}" \
    /opt/astroai/venv/ray/bin/python
check "shared image python (manager)" docker run --rm --entrypoint assert-image-python "${MGR}" \
    /opt/astroai/venv/ray/bin/python
check "manager/worker python minor match" bash -c '
  w=$(docker run --rm --entrypoint python3 "'"${WRK}"'" -c "import sys; print(\"%d.%d\" % sys.version_info[:2])")
  m=$(docker run --rm --entrypoint python3 "'"${MGR}"'" -c "import sys; print(\"%d.%d\" % sys.version_info[:2])")
  [[ "$w" == "$m" ]] && [[ "$w" == "${PYTHON_VERSION:-3.13}" ]]
'
check "ray version stamp" docker run --rm --entrypoint bash "${WRK}" -c \
    'test -f /opt/astroai/ray-version.txt && \
     [[ "$(tr -d "[:space:]" </opt/astroai/ray-version.txt)" == "$(python -c "import ray; print(ray.__version__)")" ]]'
check "network probe script" docker run --rm --entrypoint test "${WRK}" -x /opt/astroai/bin/ray-network-probe.sh
check "canfar in manager" docker run --rm --entrypoint python "${MGR}" -c "import canfar"
check "manager app loads" docker run --rm --entrypoint python "${MGR}" -c "import sys; sys.path.insert(0,'/opt/astroai/ray-manager'); import app"

if [[ "${SMOKE}" -eq 0 ]]; then
check "preflight module" docker run --rm --entrypoint python "${MGR}" -c "
import inspect
import astroai_workload.preflight as preflight
src = inspect.getsource(preflight)
assert 'passed=probe_ok' in src, 'preflight must assign passed from probe_ok'
"
check "worker image tag env" bash -c "docker run --rm --entrypoint printenv \"${MGR}\" RAY_IMAGE_TAG | grep -qx \"${TAG}\""
check "worker rejects missing env" bash -c "! docker run --rm \"${WRK}\" 2>/dev/null"
fi

echo ""
if [[ "${FAILURES}" -eq 0 ]]; then
    echo "All container checks passed."
    exit 0
fi
echo "${FAILURES} check(s) failed." >&2
exit 1
