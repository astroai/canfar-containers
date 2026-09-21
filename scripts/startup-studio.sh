#!/bin/bash -e
# AstroAI Studio: owned dsh profile `astroai` on :3080 (loopback) +
# path-rewrite proxy on :5000. See docs/STUDIO.md.

export ASTROAI_SESSION_KIND="${ASTROAI_SESSION_KIND:-studio}"
source /cadc/common-init.sh
# shellcheck disable=SC1091
source /opt/astroai/lib/skaha-proxy.sh

export PATH="/opt/astroai/venv/cadc/bin:/opt/astroai/bin:${PATH}"

DSH_PORT="${DSH_PORT:-3080}"
export DSH_PORT
export ASTROAI_STUDIO_PORT="${ASTROAI_STUDIO_PORT:-5000}"
export ASTROAI_AGENT_WIZARD_PORT="${ASTROAI_AGENT_WIZARD_PORT:-4792}"
export ASTROAI_TERMINAL_PORT="${ASTROAI_TERMINAL_PORT:-4793}"
export ASTROAI_TAB_TITLE="${ASTROAI_TAB_TITLE:-AstroAI Studio}"

# Default workspace: $SRCDIR (scratch src on CANFAR). dsh uses process.cwd()
# as defaultCwd for new sessions — so we must cd here before boot.
# Override with ASTROAI_STUDIO_CWD.
export SRCDIR="${SRCDIR:-${WORK:-${SCRATCH:-/scratch}/src}}"
export WORK="${WORK:-${SRCDIR}}"
STUDIO_CWD="${ASTROAI_STUDIO_CWD:-${SRCDIR}}"
mkdir -p "${STUDIO_CWD}" "${HOME}/.dsh" "${HOME}/.astroai/lab"
astroai_boot_log "studio cwd=${STUDIO_CWD} (SRCDIR=${SRCDIR})"
_state="${HOME}/.astroai/lab"

# Match canfar-lab studio_env(): keep pnpm store / TMPDIR off quota /arc home.
_user="${USER:-${LOGNAME:-$(id -un 2>/dev/null || echo user)}}"
if [[ -n "${SCRATCH:-}" && -d "${SCRATCH}" && -w "${SCRATCH}" ]]; then
    _studio_state="${SCRATCH}/.studio-${_user}"
elif [[ -d /scratch && -w /scratch ]]; then
    _studio_state="/scratch/.studio-${_user}"
else
    _studio_state="${TMPDIR:-/tmp}/.studio-${_user}"
    astroai_boot_log "WARN: no writable scratch — Studio state → ${_studio_state}"
fi
mkdir -p "${_studio_state}/pnpm-store" "${_studio_state}/pnpm-home" "${_studio_state}/tmp"
export ASTROAI_STUDIO_STATE="${_studio_state}"
export ASTROAI_STUDIO_PROFILE=canfar
export npm_config_store_dir="${_studio_state}/pnpm-store"
export PNPM_HOME="${_studio_state}/pnpm-home"
export TMPDIR="${_studio_state}/tmp"

# Bind :5000 immediately (marimo pattern) so Skaha Connect does not 502 while
# prepare/dsh still run. Proxy serves a 200 "starting" page until dsh is up.
_dsh_log="${_studio_state}/dsh.log"
_token_file="${_studio_state}/dsh-web-token"
: >"${_dsh_log}"
rm -f "${_token_file}"
export ASTROAI_DSH_TOKEN_FILE="${_token_file}"
python3 /opt/astroai/lib/studio-canfar-proxy.py &
PROXY_PID=$!
astroai_boot_log "studio-proxy :${ASTROAI_STUDIO_PORT} early (pid=${PROXY_PID})"

cleanup() {
    local rc=$?
    astroai_boot_log "session:exit rc=${rc}"
    kill "${PROXY_PID:-}" "${WIZARD_PID:-}" "${GHOSTTY_PID:-}" "${DSH_PID:-}" 2>/dev/null || true
    wait "${PROXY_PID:-}" "${WIZARD_PID:-}" "${GHOSTTY_PID:-}" "${DSH_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# common-init already runs `astroai agent setup` in the background for studio.
# Prepare must finish *before* dsh starts so the owned `astroai` profile exists.
# --no-install: never block Connect URL on a cold `dsh plugin` / pnpm fetch
# (up to 30 min). Team layers: re-run `astroai studio --prepare` once online.
if command -v astroai >/dev/null 2>&1; then
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
    if ! astroai --yes studio --prepare --profile canfar --no-install \
            >>"${_state}/studio-prepare.log" 2>&1; then
        astroai_boot_log "FATAL: astroai studio --prepare failed — see ${_state}/studio-prepare.log (refresh astroai-lab.lock?)"
        tail -n 40 "${_state}/studio-prepare.log" >&2 || true
        exit 1
    fi
    if grep -q 'TEAM LAYERS UNAVAILABLE\|Team layers are not active\|Team layers are off' \
            "${_state}/studio-prepare.log" 2>/dev/null; then
        astroai_boot_log "WARN: Team layers not mounted — run: astroai studio --prepare"
    fi
    if command -v npx >/dev/null 2>&1; then
        npx --yes skills add astroai/canfar-skills >/dev/null 2>&1 || true
    fi
else
    astroai_boot_log "FATAL: astroai CLI missing — cannot prepare Studio profile"
    exit 1
fi

# dsh Host fence: Origin.host must equal Host, and Host must be trusted.
# Browser Host is the public Skaha host (not the pod hostname). Trust common
# CANFAR connect hosts + optional ASTROAI_STUDIO_TRUSTED_HOST.
_DSH_TRUST=(
    --trusted-host ws-uv.canfar.net
    --trusted-host ws-uvic.canfar.net
    --trusted-host staging.canfar.net
    --trusted-host workloads.canfar.net
    --trusted-host workload-uv.canfar.net
)
if [[ -n "${ASTROAI_STUDIO_TRUSTED_HOST:-}" ]]; then
    # shellcheck disable=SC2206
    for _h in ${ASTROAI_STUDIO_TRUSTED_HOST//,/ }; do
        [[ -n "${_h}" ]] && _DSH_TRUST+=(--trusted-host "${_h}")
    done
fi
_host="$(hostname -f 2>/dev/null || hostname 2>/dev/null || true)"
if [[ -n "${_host}" ]]; then
    _DSH_TRUST+=(--trusted-host "${_host}")
fi

cd "${STUDIO_CWD}"
# Owned composition from `astroai studio --prepare` (not the stock `web` profile).
# Log to a file so we can scrape the one-shot ``?token=`` (Skaha Connect omits it).
: >"${_dsh_log}"
rm -f "${_token_file}"
dsh --profile astroai --no-open --port "${DSH_PORT}" "${_DSH_TRUST[@]}" \
    >"${_dsh_log}" 2>&1 &
DSH_PID=$!

_extract_dsh_token() {
    # dsh prints: dsh web: http://127.0.0.1:3080/?token=…
    local tok
    tok="$(grep -oE 'token=[A-Za-z0-9_-]+' "${_dsh_log}" 2>/dev/null | head -1 | cut -d= -f2- || true)"
    if [[ -n "${tok}" ]]; then
        printf '%s\n' "${tok}" >"${_token_file}"
        return 0
    fi
    return 1
}

_dsh_ready=0
for _ in $(seq 1 120); do
    # Do not use curl -f: dsh may answer 401 without ?token= while still healthy.
    if curl -sS -o /dev/null --max-time 2 "http://127.0.0.1:${DSH_PORT}/" >/dev/null 2>&1; then
        _dsh_ready=1
        _extract_dsh_token || true
        break
    fi
    if ! kill -0 "${DSH_PID}" 2>/dev/null; then
        astroai_boot_log "dsh (profile astroai) exited early (before ready)"
        exit 1
    fi
    if ! kill -0 "${PROXY_PID}" 2>/dev/null; then
        astroai_boot_log "studio-proxy exited early"
        exit 1
    fi
    _extract_dsh_token || true
    sleep 0.5
done
if [[ "${_dsh_ready}" != "1" ]]; then
    astroai_boot_log "dsh not ready on :${DSH_PORT} within 60s"
    exit 1
fi
# Token may land slightly after the listen socket opens.
for _ in $(seq 1 20); do
    _extract_dsh_token && break
    sleep 0.25
done
if [[ -s "${_token_file}" ]]; then
    astroai_boot_log "dsh web token captured for Skaha Connect redirect"
else
    astroai_boot_log "WARN: dsh web token not found — Connect URL may 401 without ?token="
fi

# AstroAI hub + ghostty-web (proxy mounts /astroai-agents/ and /astroai-terminal/).
python3 /opt/astroai/lib/agent-wizard.py &
WIZARD_PID=$!

if [[ -f /opt/ghostty-web/server.mjs ]]; then
    _term_back="/"
    if [[ -n "${skaha_sessionid:-}" ]]; then
        _term_back="/session/contrib/${skaha_sessionid}/"
    fi
    HOST=127.0.0.1 PORT="${ASTROAI_TERMINAL_PORT}" \
        ASTROAI_TAB_TITLE="${ASTROAI_TAB_TITLE:-AstroAI Studio}" \
        ASTROAI_TERMINAL_BACK_HREF="${_term_back}" \
        ASTROAI_TERMINAL_BACK_LABEL="Studio" \
        PWD="${STUDIO_CWD}" \
        node /opt/ghostty-web/server.mjs &
    GHOSTTY_PID=$!
fi

astroai_boot_log "studio dsh+proxy+sidecars ready (profile=astroai), waiting"
wait -n "${DSH_PID}" "${PROXY_PID}"
exit $?
