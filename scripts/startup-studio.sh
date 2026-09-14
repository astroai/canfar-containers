#!/bin/bash -e
# AstroAI Studio: dsh web on :3080 (loopback) + path-rewrite proxy on :5000.
# See /opt/astroai/docs or repo docs/STUDIO.md.

export ASTROAI_SESSION_KIND="${ASTROAI_SESSION_KIND:-studio}"
source /cadc/common-init.sh
# shellcheck disable=SC1091
source /opt/astroai/lib/skaha-proxy.sh

export PATH="/opt/astroai/venv/cadc/bin:/opt/astroai/bin:${PATH}"

DSH_PORT="${DSH_PORT:-3080}"
export DSH_PORT
export ASTROAI_STUDIO_PORT="${ASTROAI_STUDIO_PORT:-5000}"
export ASTROAI_AGENT_WIZARD_PORT="${ASTROAI_AGENT_WIZARD_PORT:-4792}"

# Workspace: prefer $WORK / $SRCDIR (scratch src), else home.
STUDIO_CWD="${ASTROAI_STUDIO_CWD:-${WORK:-${SRCDIR:-${HOME}}}}"
mkdir -p "${STUDIO_CWD}" "${HOME}/.dsh" "${HOME}/.astroai/lab"

# common-init already runs `astroai agent setup` in the background for studio.
# Only prepare Studio Team / dotenv here (needs agent setup to settle).
if command -v astroai >/dev/null 2>&1; then
    (
        _state="${HOME}/.astroai/lab"
        for _ in $(seq 1 180); do
            if [[ ! -f "${_state}/agent-setup-pending" ]] \
                && { [[ -f "${_state}/agent-setup-stamp" ]] || [[ -f "${_state}/agent-setup-failed" ]]; }; then
                break
            fi
            if [[ ! -f "${_state}/agent-setup-pending" ]] \
                && [[ ! -f "${_state}/agent-setup.lock" ]] \
                && [[ "${_}" -gt 5 ]]; then
                break
            fi
            sleep 1
        done
        for _ in $(seq 1 60); do
            [[ -f "${_state}/agent-setup.lock" ]] || break
            sleep 1
        done
        # Fail loudly in boot log if prepare is missing (stale canfar-lab lock).
        if ! astroai --yes studio --prepare >>"${_state}/studio-prepare.log" 2>&1; then
            astroai_boot_log "astroai studio --prepare failed — see ${_state}/studio-prepare.log (refresh astroai-lab.lock?)"
        fi
        if command -v npx >/dev/null 2>&1; then
            npx --yes skills add astroai/canfar-skills >/dev/null 2>&1 || true
        fi
    ) &
fi

python3 /opt/astroai/lib/agent-wizard.py &
WIZARD_PID=$!

# dsh Host fence: Origin.host must equal Host, and Host must be trusted.
# Browser Host is the public Skaha host (not the pod hostname). Trust common
# CANFAR connect hosts + optional ASTROAI_STUDIO_TRUSTED_HOST.
_DSH_TRUST=(
    --trusted-host ws-uv.canfar.net
    --trusted-host ws-uvic.canfar.net
    --trusted-host staging.canfar.net
)
if [[ -n "${ASTROAI_STUDIO_TRUSTED_HOST:-}" ]]; then
    _DSH_TRUST+=(--trusted-host "${ASTROAI_STUDIO_TRUSTED_HOST}")
fi
_host="$(hostname -f 2>/dev/null || hostname 2>/dev/null || true)"
if [[ -n "${_host}" ]]; then
    _DSH_TRUST+=(--trusted-host "${_host}")
fi

cd "${STUDIO_CWD}"
dsh --profile web --no-open --port "${DSH_PORT}" "${_DSH_TRUST[@]}" &
DSH_PID=$!

cleanup() {
    local rc=$?
    astroai_boot_log "session:exit rc=${rc}"
    kill "${PROXY_PID:-}" "${WIZARD_PID:-}" "${DSH_PID}" 2>/dev/null || true
    wait "${PROXY_PID:-}" "${WIZARD_PID:-}" "${DSH_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

_dsh_ready=0
for _ in $(seq 1 120); do
    if curl -fsS "http://127.0.0.1:${DSH_PORT}/" >/dev/null 2>&1; then
        _dsh_ready=1
        break
    fi
    if ! kill -0 "${DSH_PID}" 2>/dev/null; then
        astroai_boot_log "dsh web exited early (before ready)"
        exit 1
    fi
    sleep 0.5
done
if [[ "${_dsh_ready}" != "1" ]]; then
    astroai_boot_log "dsh not ready on :${DSH_PORT} within 60s"
    exit 1
fi

python3 /opt/astroai/lib/studio-canfar-proxy.py &
PROXY_PID=$!

astroai_boot_log "studio dsh+proxy ready, waiting"
wait -n "${DSH_PID}" "${PROXY_PID}"
exit $?
