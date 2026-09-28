#!/bin/bash -e
# OpenScience research workspace on port 5000 (CANFAR contributed).
# The server binds 127.0.0.1:4796 behind a per-session bearer token;
# openscience-canfar-proxy.py adds the token, mounts the AstroAI hub
# (:4792) at /astroai-agents/ and shows a starting page during restarts.

export ASTROAI_SESSION_KIND="${ASTROAI_SESSION_KIND:-openscience}"
export ASTROAI_PUBLIC_PORT="${ASTROAI_PUBLIC_PORT:-5000}"
export ASTROAI_OPENSCIENCE_PORT="${ASTROAI_OPENSCIENCE_PORT:-4796}"
export ASTROAI_AGENT_WIZARD_PORT="${ASTROAI_AGENT_WIZARD_PORT:-4792}"

# Bind :5000 before common-init: walking a large CephFS home can outlast the
# Skaha liveness probe. Session state (token, flags, logs) lives on scratch.
_os_user="${USER:-${LOGNAME:-$(id -un 2>/dev/null || echo user)}}"
if [[ -n "${SCRATCH:-}" && -d "${SCRATCH}" && -w "${SCRATCH}" ]]; then
    _os_state="${SCRATCH}/.openscience-${_os_user}"
else
    _os_state="${TMPDIR:-/tmp}/.openscience-${_os_user}"
fi
export ASTROAI_OPENSCIENCE_STATE="${ASTROAI_OPENSCIENCE_STATE:-${_os_state}}"
_os_state="${ASTROAI_OPENSCIENCE_STATE}"
mkdir -p "${_os_state}"
rm -f "${_os_state}/openscience.restart" "${_os_state}/openscience.failed"
_os_token="${_os_state}/openscience-token"
(umask 077 && head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' >"${_os_token}")
_os_log="${_os_state}/openscience.log"

python3 /opt/astroai/lib/openscience-canfar-proxy.py >>"${_os_state}/proxy.log" 2>&1 &
PROXY_PID=$!
echo "[astroai-boot] openscience-proxy :${ASTROAI_PUBLIC_PORT} pre-init (pid=${PROXY_PID})" >&2

source /cadc/common-init.sh
# shellcheck disable=SC1091
source /opt/astroai/lib/skaha-proxy.sh

export PATH="/opt/astroai/venv/cadc/bin:/opt/astroai/bin:${PATH}"
# Projects are opened from the UI; start where persistent files live.
_os_cwd="${ASTROAI_OPENSCIENCE_CWD:-${HOME}}"

cleanup() {
    local rc=$?
    astroai_boot_log "session:exit rc=${rc}"
    kill "${PROXY_PID:-}" "${WIZARD_PID:-}" "${OPENSCIENCE_PID:-}" 2>/dev/null || true
    wait "${PROXY_PID:-}" "${WIZARD_PID:-}" "${OPENSCIENCE_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

_LAB_BIN="canfar-lab"
if ! command -v "${_LAB_BIN}" >/dev/null 2>&1; then
    _LAB_BIN="/opt/astroai/venv/cadc/bin/canfar-lab"
fi
# MCP tools, instructions, skills, approvals and sandbox defaults (merged, never
# clobbering user settings).
"${_LAB_BIN}" --yes agent setup openscience >>"${_os_log}" 2>&1 || \
    astroai_boot_log "agent setup openscience failed — see ${_os_log}"

python3 /opt/astroai/lib/agent-wizard.py >>"${_os_state}/wizard.log" 2>&1 &
WIZARD_PID=$!

_fast_exits=0
_started=0
OPENSCIENCE_PID=""
_start_openscience() {
    rm -f "${_os_state}/openscience.restart" "${_os_state}/openscience.failed"
    astroai_boot_log "starting openscience on :${ASTROAI_OPENSCIENCE_PORT}"
    (
        cd "${_os_cwd}" || exit 1
        OPENSCIENCE_AUTH_TOKEN="$(cat "${_os_token}")" \
            exec /opt/astroai/bin/openscience serve --port "${ASTROAI_OPENSCIENCE_PORT}"
    ) >>"${_os_log}" 2>&1 &
    OPENSCIENCE_PID=$!
    _started=${SECONDS}
}

# Supervise: restart when the hub saves model keys (openscience.restart), give
# up after three exits within 30 s of starting (openscience.failed; the proxy's
# "Try again" link or a key change clears it).
astroai_boot_log "openscience session ready, supervising"
while true; do
    if ! kill -0 "${PROXY_PID}" 2>/dev/null; then
        astroai_boot_log "proxy exited"
        exit 1
    fi
    if [[ -n "${OPENSCIENCE_PID}" ]] && kill -0 "${OPENSCIENCE_PID}" 2>/dev/null; then
        if [[ -f "${_os_state}/openscience.restart" ]]; then
            astroai_boot_log "model keys changed — restarting openscience"
            kill "${OPENSCIENCE_PID}" 2>/dev/null || true
            wait "${OPENSCIENCE_PID}" 2>/dev/null || true
            _start_openscience
        fi
    else
        if [[ -n "${OPENSCIENCE_PID}" ]]; then
            # bash -e: a crashed server's status must not end the session.
            wait "${OPENSCIENCE_PID}" 2>/dev/null || true
            OPENSCIENCE_PID=""
            if ((SECONDS - _started < 30)); then
                _fast_exits=$((_fast_exits + 1))
            else
                _fast_exits=0
            fi
            if ((_fast_exits >= 3)); then
                astroai_boot_log "openscience exited ${_fast_exits} times in a row — see ${_os_log}"
                touch "${_os_state}/openscience.failed"
                _fast_exits=0
            fi
        fi
        if [[ -f "${_os_state}/openscience.restart" || ! -f "${_os_state}/openscience.failed" ]]; then
            _start_openscience
        fi
    fi
    if ! kill -0 "${WIZARD_PID}" 2>/dev/null; then
        wait "${WIZARD_PID}" 2>/dev/null || true
        python3 /opt/astroai/lib/agent-wizard.py >>"${_os_state}/wizard.log" 2>&1 &
        WIZARD_PID=$!
    fi
    sleep 2
done
