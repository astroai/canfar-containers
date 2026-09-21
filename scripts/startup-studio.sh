#!/bin/bash -e
# AstroAI Studio: owned dsh profile `astroai` on :3080 (loopback) +
# path-rewrite proxy on :5000. See docs/STUDIO.md.

export ASTROAI_SESSION_KIND="${ASTROAI_SESSION_KIND:-studio}"
export PATH="/opt/astroai/venv/cadc/bin:/opt/astroai/bin:${PATH}"

DSH_PORT="${DSH_PORT:-3080}"
export DSH_PORT
export ASTROAI_STUDIO_PORT="${ASTROAI_STUDIO_PORT:-5000}"
export ASTROAI_AGENT_WIZARD_PORT="${ASTROAI_AGENT_WIZARD_PORT:-4792}"
export ASTROAI_TERMINAL_PORT="${ASTROAI_TERMINAL_PORT:-4793}"
export ASTROAI_TAB_TITLE="${ASTROAI_TAB_TITLE:-AstroAI Studio}"

# Bind :5000 BEFORE common-init. On CANFAR, walking a large CephFS home in
# common-init can exceed Skaha liveness (connection refused on :5000) and
# crash-loop the pod before the proxy ever starts.
_user="${USER:-${LOGNAME:-$(id -un 2>/dev/null || echo user)}}"
if [[ -n "${SCRATCH:-}" && -d "${SCRATCH}" && -w "${SCRATCH}" ]]; then
    _studio_state="${SCRATCH}/.studio-${_user}"
elif [[ -d /scratch && -w /scratch ]]; then
    _studio_state="/scratch/.studio-${_user}"
else
    _studio_state="${TMPDIR:-/tmp}/.studio-${_user}"
fi
mkdir -p "${_studio_state}/pnpm-store" "${_studio_state}/pnpm-home" "${_studio_state}/tmp" \
    "${HOME:-/tmp}/.astroai/lab" 2>/dev/null || mkdir -p "${_studio_state}"
export ASTROAI_STUDIO_STATE="${_studio_state}"
export ASTROAI_STUDIO_PROFILE=canfar
export npm_config_store_dir="${_studio_state}/pnpm-store"
export PNPM_HOME="${_studio_state}/pnpm-home"
export TMPDIR="${_studio_state}/tmp"
_dsh_log="${_studio_state}/dsh.log"
_token_file="${_studio_state}/dsh-web-token"
: >"${_dsh_log}" 2>/dev/null || true
rm -f "${_token_file}"
export ASTROAI_DSH_TOKEN_FILE="${_token_file}"
python3 /opt/astroai/lib/studio-canfar-proxy.py &
PROXY_PID=$!
echo "[astroai-boot] studio-proxy :${ASTROAI_STUDIO_PORT} pre-init (pid=${PROXY_PID})" >&2

source /cadc/common-init.sh
# shellcheck disable=SC1091
source /opt/astroai/lib/skaha-proxy.sh

# Default workspace: $SRCDIR (scratch src on CANFAR). dsh uses process.cwd()
# as defaultCwd for new sessions — so we must cd here before boot.
# Override with ASTROAI_STUDIO_CWD.
export SRCDIR="${SRCDIR:-${WORK:-${SCRATCH:-/scratch}/src}}"
export WORK="${WORK:-${SRCDIR}}"
STUDIO_CWD="${ASTROAI_STUDIO_CWD:-${SRCDIR}}"
mkdir -p "${STUDIO_CWD}" "${HOME}/.dsh" "${HOME}/.astroai/lab"
astroai_boot_log "studio cwd=${STUDIO_CWD} (SRCDIR=${SRCDIR})"
_state="${HOME}/.astroai/lab"
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
    # Never block Connect on skills.sh network fetch (can hang minutes on cold
    # npm). Install in the background after prepare returns.
    if command -v npx >/dev/null 2>&1; then
        (npx --yes skills add astroai/canfar-skills >/dev/null 2>&1 || true) &
    fi
else
    astroai_boot_log "FATAL: astroai CLI missing — cannot prepare Studio profile"
    exit 1
fi

# prepare writes discovered provider keys into ~/.astroai/lab/.env (and
# agent-env.sh). Source them into this shell so dsh inherits DEEPSEEK_*/OPENAI_*/
# OPENCODE_*/… — otherwise Models stays "missing" and the SPA prompts for a key
# even when the user already configured one on the home volume.
if [[ -f "${HOME}/.astroai/lab/agent-env.sh" ]]; then
    # shellcheck disable=SC1091
    source "${HOME}/.astroai/lab/agent-env.sh"
elif [[ -f "${HOME}/.astroai/lab/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${HOME}/.astroai/lab/.env"
    set +a
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
# Persist dsh.log on $HOME so crash-loop restarts still leave a trail (scratch dies).
_dsh_log="${_state}/dsh.log"
_token_file="${_studio_state}/dsh-web-token"
export ASTROAI_DSH_TOKEN_FILE="${_token_file}"
: >"${_dsh_log}"
rm -f "${_token_file}"

_dsh_home="${DSH_HOME:-${HOME}/.dsh}"
mkdir -p "${_dsh_home}"

# dsh-atomic-write leaves `<file>.lock` (pid inside) and never removes orphans —
# "orphan recovery is an operator action". Prior Skaha crash-loops leave
# ~/.dsh/.credentials.yaml.lock on CephFS; the next boot then times out (~2s
# default, longer when waitMs is raised) and Connect sticks on STARTING.
_clear_stale_dsh_locks() {
    local lock pid
    shopt -s nullglob
    for lock in "${_dsh_home}"/*.lock "${_dsh_home}"/.*.lock; do
        [[ -f "${lock}" ]] || continue
        pid="$(tr -dc '0-9' <"${lock}" 2>/dev/null | head -c 16 || true)"
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
            continue
        fi
        rm -f "${lock}" && astroai_boot_log "removed stale dsh lock $(basename "${lock}")"
    done
    shopt -u nullglob
}

_dump_dsh_log() {
    local reason="${1:-dsh.log}"
    if [[ -s "${_dsh_log}" ]]; then
        astroai_boot_log "${reason}:"
        tail -n 60 "${_dsh_log}" | while IFS= read -r _line; do
            astroai_boot_log "  ${_line}"
        done
    else
        astroai_boot_log "${reason}: empty"
    fi
}

_stop_dsh() {
    if [[ -n "${DSH_PID:-}" ]] && kill -0 "${DSH_PID}" 2>/dev/null; then
        kill "${DSH_PID}" 2>/dev/null || true
        wait "${DSH_PID}" 2>/dev/null || true
    fi
    # Reap anything still bound to :DSH_PORT (restart races).
    if command -v fuser >/dev/null 2>&1; then
        fuser -k "${DSH_PORT}/tcp" 2>/dev/null || true
    fi
    DSH_PID=""
}

_start_dsh() {
    _stop_dsh
    _clear_stale_dsh_locks
    astroai_boot_log "starting dsh --profile astroai on :${DSH_PORT}"
    # Line-buffer when possible so the launch ?token= line hits dsh.log before
    # any later crash (node often fully-buffers when stdout is a file).
    if command -v stdbuf >/dev/null 2>&1; then
        stdbuf -oL -eL dsh --profile astroai --no-open --port "${DSH_PORT}" \
            "${_DSH_TRUST[@]}" >"${_dsh_log}" 2>&1 &
    else
        dsh --profile astroai --no-open --port "${DSH_PORT}" "${_DSH_TRUST[@]}" \
            >"${_dsh_log}" 2>&1 &
    fi
    DSH_PID=$!
    astroai_boot_log "dsh pid=${DSH_PID}"
}

_extract_dsh_token() {
    # dsh prints: dsh web: http://127.0.0.1:3080/?token=…
    # Tolerate ANSI, alternate separators, and delayed flushes on CephFS.
    local tok
    tok="$(
        grep -aoE 'token[=:][A-Za-z0-9_-]+' "${_dsh_log}" 2>/dev/null \
            | head -1 | sed -E 's/^token[=:]//' || true
    )"
    if [[ -z "${tok}" ]]; then
        tok="$(
            sed -nE 's/.*[?&]token=([A-Za-z0-9_-]+).*/\1/p' "${_dsh_log}" 2>/dev/null \
                | head -1 || true
        )"
    fi
    if [[ -n "${tok}" ]]; then
        printf '%s\n' "${tok}" >"${_token_file}"
        return 0
    fi
    return 1
}

_start_dsh

_dsh_ready=0
for _ in $(seq 1 180); do
    # Do not use curl -f: dsh may answer 401 without ?token= while still healthy.
    if curl -sS -o /dev/null --max-time 2 "http://127.0.0.1:${DSH_PORT}/" >/dev/null 2>&1; then
        _dsh_ready=1
        _extract_dsh_token || true
        break
    fi
    if ! kill -0 "${PROXY_PID}" 2>/dev/null; then
        astroai_boot_log "studio-proxy exited early — restarting proxy"
        python3 /opt/astroai/lib/studio-canfar-proxy.py &
        PROXY_PID=$!
    fi
    if ! kill -0 "${DSH_PID}" 2>/dev/null; then
        # Never exit 1 here: that kills :5000 and Skaha crash-loops the pod.
        astroai_boot_log "dsh exited before ready — dumping log and restarting"
        _dump_dsh_log "dsh.log"
        sleep 2
        _start_dsh
    fi
    _extract_dsh_token || true
    sleep 0.5
done
if [[ "${_dsh_ready}" != "1" ]]; then
    astroai_boot_log "WARN: dsh not ready on :${DSH_PORT} within 90s — keeping proxy up"
    _dump_dsh_log "dsh.log tail"
fi
# Token often lands after the listen socket opens (slow home / CephFS). Wait up
# to ~60s; keep a background scavenger for even later flushes.
for _ in $(seq 1 120); do
    _extract_dsh_token && break
    if ! kill -0 "${DSH_PID}" 2>/dev/null; then
        astroai_boot_log "dsh died during token wait — restarting"
        _dump_dsh_log "dsh.log"
        _start_dsh
    fi
    sleep 0.5
done
if [[ -s "${_token_file}" ]]; then
    astroai_boot_log "dsh web token captured for Skaha Connect redirect"
else
    astroai_boot_log "WARN: dsh web token not found yet — proxy stays on starting page"
    _dump_dsh_log "dsh.log (no token)"
    (
        # Token-only scavenger: do NOT restart dsh here — the foreground
        # supervisor owns that. Dual restarts race on ~/.dsh/*.lock.
        for _ in $(seq 1 600); do
            if _extract_dsh_token; then
                astroai_boot_log "dsh web token captured (late) for Skaha Connect redirect"
                exit 0
            fi
            sleep 1
        done
    ) &
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
# Supervise forever: never let a dsh crash take down :5000 (Skaha liveness).
while true; do
    if ! kill -0 "${PROXY_PID}" 2>/dev/null; then
        astroai_boot_log "studio-proxy died — restarting"
        python3 /opt/astroai/lib/studio-canfar-proxy.py &
        PROXY_PID=$!
    fi
    if ! kill -0 "${DSH_PID}" 2>/dev/null; then
        astroai_boot_log "dsh died — restarting"
        _dump_dsh_log "dsh.log"
        _start_dsh
    fi
    if [[ -n "${WIZARD_PID:-}" ]] && ! kill -0 "${WIZARD_PID}" 2>/dev/null; then
        python3 /opt/astroai/lib/agent-wizard.py &
        WIZARD_PID=$!
    fi
    if [[ -n "${GHOSTTY_PID:-}" ]] && ! kill -0 "${GHOSTTY_PID}" 2>/dev/null; then
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
    fi
    _extract_dsh_token || true
    sleep 2
done
