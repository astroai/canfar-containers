#!/bin/bash -e
# Skaha entrypoint — Ray head + manager UI on port 5000.

set -o pipefail

export ASTROAI_SESSION_KIND="${ASTROAI_SESSION_KIND:-ray-manager}"

if [[ -f /opt/astroai/lib/astroai-env-common.sh ]]; then
    # shellcheck disable=SC1091
    source /opt/astroai/lib/astroai-env-common.sh
fi
if ! declare -F astroai_boot_log >/dev/null 2>&1; then
    astroai_boot_log() { echo "[astroai-boot] $*" >&2 || true; }
fi
trap 'astroai_boot_log "ray-manager:ERR line=${LINENO} rc=$? cmd=${BASH_COMMAND}"' ERR

astroai_boot_log "ray-manager:start"

if [[ -f /etc/profile.d/astroai.sh ]]; then
    # shellcheck disable=SC1091
    source /etc/profile.d/astroai.sh
fi

# Per-session manager overrides (e.g. RAY_AUTOSCALING_ENABLED=1) live on the
# user home because Skaha does not pass -e env to contributed sessions. The
# `astroai autoscaler` can then be enabled for a single manager session
# by writing ~/.config/canfar/lab/ray-manager.env from a webterm (or by the
# test harness bootstrap) before launching the manager.
if [[ -f "${HOME}/.config/canfar/lab/ray-manager.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${HOME}/.config/canfar/lab/ray-manager.env"
    set +a
fi

export RAY_CLUSTER_ID="${RAY_CLUSTER_ID:-}"
if [[ -z "${RAY_CLUSTER_ID}" ]]; then
    # Bind state to this manager session so /arc/home does not reuse another
    # pod's clusters/default leftovers across contributed manager launches.
    _sid="${skaha_sessionid:-${SKAHA_SESSIONID:-}}"
    if [[ -n "${_sid}" ]]; then
        export RAY_CLUSTER_ID="mgr-${_sid}"
    else
        export RAY_CLUSTER_ID="default"
    fi
fi
# Jobs / Dashboard API is local to this pod — RayExecutor() reads this.
export ASTROAI_RAY_JOBS_ADDRESS="${ASTROAI_RAY_JOBS_ADDRESS:-http://127.0.0.1:8265}"
# shellcheck disable=SC1091
source /opt/astroai/lib/ray-version.sh
export RAY_VERSION_EXPECTED="$(ray_version_expected)"
export RAY_HEAD_PORT="${RAY_HEAD_PORT:-6379}"
export RAY_IMAGE_TAG="${RAY_IMAGE_TAG:-${BUILD_TAG:-${TAG:-local}}}"
export RAY_NODE_IP_ADDRESS="${RAY_NODE_IP_ADDRESS:-$(hostname -i | awk '{print $1}')}"

# Workers/preflight are launched via the canfar client using
# /arc/home/<user>/.canfar. If that config still has active.server=canfar
# (production) while this manager pod was started on staging, workers land on
# the wrong cluster and cannot reach the head. Honor an explicit pin from
# session env.
_srv="${CANFAR_ACTIVE_SERVER:-${ACTIVE_SERVER:-}}"
if [[ -n "${_srv}" ]]; then
    CANFAR_BIN="$(command -v canfar || true)"
    if [[ -z "${CANFAR_BIN}" && -x /opt/astroai/venv/cadc/bin/canfar ]]; then
        CANFAR_BIN=/opt/astroai/venv/cadc/bin/canfar
    fi
    if [[ -n "${CANFAR_BIN}" ]]; then
        if ! "${CANFAR_BIN}" config set active.server "${_srv}" >/dev/null 2>&1; then
            case "${_srv}" in
                staging) _url="${ACTIVE_SERVER_URL:-https://staging.canfar.net/skaha}" ;;
                canfar)  _url="${ACTIVE_SERVER_URL:-https://ws-uv.canfar.net/skaha}" ;;
                *)       _url="${ACTIVE_SERVER_URL:-}" ;;
            esac
            if [[ -n "${_url}" ]]; then
                "${CANFAR_BIN}" config set "servers.${_srv}.url" "${_url}" >/dev/null 2>&1 || true
                "${CANFAR_BIN}" config set "servers.${_srv}.name" "${_srv}" >/dev/null 2>&1 || true
                "${CANFAR_BIN}" config set "servers.${_srv}.version" "v1" >/dev/null 2>&1 || true
                "${CANFAR_BIN}" config set "servers.${_srv}.idp" "cadc" >/dev/null 2>&1 || true
                "${CANFAR_BIN}" config set active.server "${_srv}" >/dev/null 2>&1 || true
            fi
        fi
        if "${CANFAR_BIN}" auth show 2>/dev/null | grep -q "${_srv}"; then
            echo "CANFAR active.server pinned to ${_srv}"
        else
            echo "Warning: could not pin canfar active.server=${_srv}" >&2
        fi
    fi
fi

state_dir="${HOME}/.astroai/ray/clusters/${RAY_CLUSTER_ID}"
mkdir -p "${state_dir}"
export RAY_MANAGER_HEARTBEAT_PATH="${state_dir}/manager-heartbeat"
touch "${RAY_MANAGER_HEARTBEAT_PATH}"

(while true; do touch "${RAY_MANAGER_HEARTBEAT_PATH}"; sleep 5; done) &

astroai_boot_log "ray-manager:cluster=${RAY_CLUSTER_ID} jobs=${ASTROAI_RAY_JOBS_ADDRESS}"
echo "CANFAR Ray Manager starting (cluster ${RAY_CLUSTER_ID})"
trap - ERR
astroai_boot_log "exec uvicorn :5000"
exec python -m uvicorn app:app --host 0.0.0.0 --port 5000 --app-dir /opt/astroai/ray-manager
