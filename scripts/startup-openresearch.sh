#!/bin/bash -e
# OpenResearch (orx) dashboard on port 5000 (CANFAR contributed).
# Upstream binds 127.0.0.1:4791; canfar proxy rewrites absolute /api /assets
# paths so the SPA works under /session/contrib/<id>/.

export ASTROAI_SESSION_KIND="${ASTROAI_SESSION_KIND:-openresearch}"
source /cadc/common-init.sh
# shellcheck disable=SC1091
source /opt/astroai/lib/skaha-proxy.sh

export ORX_NO_UPDATE_CHECK=1
export PATH="/opt/astroai/venv/cadc/bin:/opt/astroai/bin:${PATH}"

# orx persists local store under XDG data home.
mkdir -p "${XDG_DATA_HOME:-${HOME}/.local/share}/openresearch" \
    "${XDG_CONFIG_HOME:-${HOME}/.config}/openresearch" \
    "${XDG_CACHE_HOME:-${SCRATCH:-/tmp}}/openresearch"

# Best-effort: default OpenResearch compute to CANFAR batch (Ray under the hood).
python3 /opt/astroai/lib/orx-wire-compute.py >/dev/null 2>&1 || true

# Best-effort: drop OpenResearch skills after core agent setup (avoid lock races).
if command -v orx >/dev/null 2>&1; then
    orx --no-telemetry telemetry off >/dev/null 2>&1 || true
    (
        _state="${HOME}/.astroai/lab"
        # Wait until bg setup finished: pending cleared and (stamp|failed) present,
        # or timeout. Then wait out any remaining lock.
        for _ in $(seq 1 180); do
            if [[ ! -f "${_state}/agent-setup-pending" ]] \
                && { [[ -f "${_state}/agent-setup-stamp" ]] || [[ -f "${_state}/agent-setup-failed" ]]; }; then
                break
            fi
            # No auto-setup this session (pending never created) — don't wait forever.
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
        orx --no-telemetry install-skills >/dev/null 2>&1 || true
    ) &
fi

ORX_PORT="${ORX_PORT:-4791}"
export ORX_PORT
export ASTROAI_OPENRESEARCH_PORT="${ASTROAI_OPENRESEARCH_PORT:-5000}"
export ASTROAI_AGENT_WIZARD_PORT="${ASTROAI_AGENT_WIZARD_PORT:-4792}"

# AstroAI agent wizard — never block orx if it fails.
python3 /opt/astroai/lib/agent-wizard.py &
WIZARD_PID=$!

orx --no-telemetry up --port "${ORX_PORT}" --no-browser &
ORX_PID=$!

cleanup() {
    local rc=$?
    astroai_boot_log "session:exit rc=${rc}"
    kill "${PROXY_PID:-}" "${WIZARD_PID:-}" "${ORX_PID}" 2>/dev/null || true
    wait "${PROXY_PID:-}" "${WIZARD_PID:-}" "${ORX_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait until orx is accepting connections.
_orx_ready=0
for _ in $(seq 1 90); do
    if curl -fsS "http://127.0.0.1:${ORX_PORT}/" >/dev/null 2>&1; then
        _orx_ready=1
        break
    fi
    if ! kill -0 "${ORX_PID}" 2>/dev/null; then
        astroai_boot_log "orx up exited early (before ready)"
        exit 1
    fi
    sleep 0.5
done
if [[ "${_orx_ready}" != "1" ]]; then
    astroai_boot_log "orx not ready on :${ORX_PORT} within 90s"
    exit 1
fi

# Path-rewriting reverse proxy (not raw TCP) so absolute /assets and /api
# URLs stay under /session/contrib/<skaha_sessionid>/.
python3 /opt/astroai/lib/orx-canfar-proxy.py &
PROXY_PID=$!

# Main UI + proxy; wizard exit must not take down the session.
astroai_boot_log "orx+proxy ready, waiting"
wait -n "${ORX_PID}" "${PROXY_PID}"
exit $?
