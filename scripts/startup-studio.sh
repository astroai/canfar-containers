#!/bin/bash -e
# AstroAI Studio: Unified 5-in-1 development studio for CANFAR.
# Multiplexes DeepSeek Harness (:3080), Ghostty terminal (:4793),
# JupyterLab (:8888), Marimo (:2718), OpenVSCode (:8080), and Compute Hub (:4792)
# over public port 5000 via studio-canfar-proxy.py.

export ASTROAI_SESSION_KIND="${ASTROAI_SESSION_KIND:-studio}"
export PATH="/opt/astroai/venv/cadc/bin:/opt/openvscode-server/bin:/opt/astroai/bin:${PATH}"

DSH_PORT="${DSH_PORT:-3080}"
export DSH_PORT
export ASTROAI_STUDIO_PORT="${ASTROAI_STUDIO_PORT:-5000}"
export ASTROAI_AGENT_WIZARD_PORT="${ASTROAI_AGENT_WIZARD_PORT:-4792}"
export ASTROAI_TERMINAL_PORT="${ASTROAI_TERMINAL_PORT:-4793}"
export ASTROAI_JUPYTER_PORT="${ASTROAI_JUPYTER_PORT:-8888}"
export ASTROAI_MARIMO_PORT="${ASTROAI_MARIMO_PORT:-2718}"
export ASTROAI_VSCODE_PORT="${ASTROAI_VSCODE_PORT:-8080}"
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
    "${_studio_state}/logs" "${_studio_state}/jupyter-runtime" "${_studio_state}/jupyter-data" \
    "${_studio_state}/vscode-data" "${_studio_state}/vscode-extensions" \
    "${HOME:-/tmp}/.canfar/lab" 2>/dev/null || mkdir -p "${_studio_state}"
export ASTROAI_STUDIO_STATE="${_studio_state}"
export ASTROAI_STUDIO_PROFILE=canfar
export npm_config_store_dir="${_studio_state}/pnpm-store"
export PNPM_HOME="${_studio_state}/pnpm-home"
export TMPDIR="${_studio_state}/tmp"
_dsh_log="${_studio_state}/logs/dsh.log"
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
export SRCDIR="${SRCDIR:-${WORK:-${SCRATCH:-/scratch}/src}}"
export WORK="${WORK:-${SRCDIR}}"
STUDIO_CWD="${ASTROAI_STUDIO_CWD:-${SRCDIR}}"
mkdir -p "${STUDIO_CWD}" "${HOME}/.dsh" "${HOME}/.canfar/lab"
astroai_boot_log "studio cwd=${STUDIO_CWD} (SRCDIR=${SRCDIR})"
_state="${HOME}/.canfar/lab"
astroai_boot_log "studio-proxy :${ASTROAI_STUDIO_PORT} early (pid=${PROXY_PID})"

cleanup() {
    local rc=$?
    astroai_boot_log "session:exit rc=${rc}"
    kill "${PROXY_PID:-}" "${WIZARD_PID:-}" "${GHOSTTY_PID:-}" "${DSH_PID:-}" \
         "${JUPYTER_PID:-}" "${MARIMO_PID:-}" "${VSCODE_PID:-}" 2>/dev/null || true
    wait "${PROXY_PID:-}" "${WIZARD_PID:-}" "${GHOSTTY_PID:-}" "${DSH_PID:-}" \
         "${JUPYTER_PID:-}" "${MARIMO_PID:-}" "${VSCODE_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Resolve canfar-lab CLI (never legacy astroai)
_LAB_BIN="canfar-lab"
if ! command -v "${_LAB_BIN}" >/dev/null 2>&1; then
    if [[ -x /opt/astroai/venv/cadc/bin/canfar-lab ]]; then
        _LAB_BIN="/opt/astroai/venv/cadc/bin/canfar-lab"
    elif [[ -x /opt/astroai/venv/cadc/bin/astroai ]]; then
        _LAB_BIN="/opt/astroai/venv/cadc/bin/astroai"
    fi
fi

# Prepare studio profile before services start
if [[ -n "${_LAB_BIN}" ]] && command -v "${_LAB_BIN}" >/dev/null 2>&1; then
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
    if ! "${_LAB_BIN}" --yes studio --prepare --profile canfar --no-install \
            >>"${_state}/studio-prepare.log" 2>&1; then
        astroai_boot_log "WARN: ${_LAB_BIN} studio --prepare returned non-zero — check ${_state}/studio-prepare.log"
    fi
    if command -v npx >/dev/null 2>&1; then
        (npx --yes skills add astroai/canfar-skills >/dev/null 2>&1 || true) &
    fi
else
    astroai_boot_log "INFO: canfar-lab CLI not pre-installed — proceeding with core services"
fi

if [[ -f "${HOME}/.canfar/lab/agent-env.sh" ]]; then
    # shellcheck disable=SC1091
    source "${HOME}/.canfar/lab/agent-env.sh"
elif [[ -f "${HOME}/.canfar/lab/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${HOME}/.canfar/lab/.env"
    set +a
elif [[ -f "${HOME}/.astroai/lab/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${HOME}/.astroai/lab/.env"
    set +a
fi

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
_dsh_home="${DSH_HOME:-${HOME}/.dsh}"
mkdir -p "${_dsh_home}"

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
    if command -v fuser >/dev/null 2>&1; then
        fuser -k "${DSH_PORT}/tcp" 2>/dev/null || true
    fi
    DSH_PID=""
}

_start_dsh() {
    _stop_dsh
    _clear_stale_dsh_locks
    astroai_boot_log "starting dsh --profile astroai on :${DSH_PORT}"
    if command -v stdbuf >/dev/null 2>&1; then
        stdbuf -oL -eL dsh --profile astroai --no-open --port "${DSH_PORT}" \
            "${_DSH_TRUST[@]}" >>"${_dsh_log}" 2>&1 &
    else
        dsh --profile astroai --no-open --port "${DSH_PORT}" "${_DSH_TRUST[@]}" \
            >>"${_dsh_log}" 2>&1 &
    fi
    DSH_PID=$!
    astroai_boot_log "dsh pid=${DSH_PID}"
}

_extract_dsh_token() {
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

# 1. Start DSH (Coding agent)
_start_dsh

# 2. Start Ghostty-web terminal
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
        node /opt/ghostty-web/server.mjs >>"${_studio_state}/logs/ghostty.log" 2>&1 &
    GHOSTTY_PID=$!
    astroai_boot_log "ghostty-web started on :${ASTROAI_TERMINAL_PORT} (pid=${GHOSTTY_PID})"
fi

# 3. Start JupyterLab (port 8888)
_start_jupyter() {
    if command -v jupyter >/dev/null 2>&1; then
        export JUPYTER_CONFIG_DIR="${_studio_state}/jupyter-config"
        export JUPYTER_RUNTIME_DIR="${_studio_state}/jupyter-runtime"
        export JUPYTER_DATA_DIR="${_studio_state}/jupyter-data"
        mkdir -p "${JUPYTER_CONFIG_DIR}" "${JUPYTER_RUNTIME_DIR}" "${JUPYTER_DATA_DIR}"
        local _jbase=""
        if [[ -n "${skaha_sessionid:-}" ]]; then
            _jbase="/session/contrib/${skaha_sessionid}/jupyter/"
        else
            _jbase="/jupyter/"
        fi
        local _jlog="${_studio_state}/logs/jupyter.log"
        astroai_boot_log "starting jupyter lab on :${ASTROAI_JUPYTER_PORT} (base_url=${_jbase})"
        jupyter lab \
            --ip 127.0.0.1 \
            --port "${ASTROAI_JUPYTER_PORT}" \
            --no-browser \
            --config /etc/jupyter/jupyter_server_config.py \
            --ServerApp.token='' \
            --ServerApp.password='' \
            --ServerApp.base_url="${_jbase}" \
            --ServerApp.root_dir="${STUDIO_CWD}" \
            --ServerApp.log_level=WARN \
            >>"${_jlog}" 2>&1 &
        JUPYTER_PID=$!
    fi
}
_start_jupyter

# 4. Start Marimo (port 2718)
_start_marimo() {
    if command -v marimo >/dev/null 2>&1; then
        local _mbase=""
        if [[ -n "${skaha_sessionid:-}" ]]; then
            _mbase="/session/contrib/${skaha_sessionid}/marimo/"
        else
            _mbase="/marimo/"
        fi
        local _mlog="${_studio_state}/logs/marimo.log"
        local _nbdir="${STUDIO_CWD}/notebooks"
        mkdir -p "${_nbdir}"
        if [[ -f "/opt/astroai/notebooks/starter.py" && ! -e "${_nbdir}/starter.py" ]]; then
            cp "/opt/astroai/notebooks/starter.py" "${_nbdir}/starter.py" 2>/dev/null || true
        fi
        astroai_boot_log "starting marimo on :${ASTROAI_MARIMO_PORT} (base_url=${_mbase})"
        marimo --log-level warn edit \
            --no-token \
            --port "${ASTROAI_MARIMO_PORT}" \
            --host 127.0.0.1 \
            --skip-update-check \
            --headless \
            --base-url "${_mbase}" \
            "${_nbdir}" >>"${_mlog}" 2>&1 &
        MARIMO_PID=$!
    fi
}
_start_marimo

# 5. Start OpenVSCode Server (port 8080)
_start_vscode() {
    if [[ -x /opt/openvscode-server/bin/openvscode-server ]]; then
        local _vbase=""
        if [[ -n "${skaha_sessionid:-}" ]]; then
            _vbase="/session/contrib/${skaha_sessionid}/vscode"
        else
            _vbase="/vscode"
        fi
        local _vlog="${_studio_state}/logs/vscode.log"
        astroai_boot_log "starting openvscode-server on :${ASTROAI_VSCODE_PORT} (base_path=${_vbase})"
        /opt/openvscode-server/bin/openvscode-server \
            --host 127.0.0.1 \
            --port "${ASTROAI_VSCODE_PORT}" \
            --without-connection-token \
            --server-base-path "${_vbase}" \
            --user-data-dir "${_studio_state}/vscode-data" \
            --extensions-dir "${_studio_state}/vscode-extensions" \
            --default-folder "${STUDIO_CWD}" \
            >>"${_vlog}" 2>&1 &
        VSCODE_PID=$!
    fi
}
_start_vscode

# 6. Compute & Agent Wizard Hub (port 4792)
if [[ -f /opt/astroai/lib/agent-wizard.py ]]; then
    python3 /opt/astroai/lib/agent-wizard.py >>"${_studio_state}/logs/wizard.log" 2>&1 &
    WIZARD_PID=$!
fi

astroai_boot_log "studio 5-in-1 workbench ready, entering supervision loop"

# Supervise forever: ensure all services stay alive
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
                node /opt/ghostty-web/server.mjs >>"${_studio_state}/logs/ghostty.log" 2>&1 &
            GHOSTTY_PID=$!
        fi
    fi
    if [[ -n "${JUPYTER_PID:-}" ]] && ! kill -0 "${JUPYTER_PID}" 2>/dev/null; then
        astroai_boot_log "jupyter died — restarting"
        _start_jupyter
    fi
    if [[ -n "${MARIMO_PID:-}" ]] && ! kill -0 "${MARIMO_PID}" 2>/dev/null; then
        astroai_boot_log "marimo died — restarting"
        _start_marimo
    fi
    if [[ -n "${VSCODE_PID:-}" ]] && ! kill -0 "${VSCODE_PID}" 2>/dev/null; then
        astroai_boot_log "vscode died — restarting"
        _start_vscode
    fi
    if [[ -n "${WIZARD_PID:-}" ]] && ! kill -0 "${WIZARD_PID}" 2>/dev/null; then
        python3 /opt/astroai/lib/agent-wizard.py >>"${_studio_state}/logs/wizard.log" 2>&1 &
        WIZARD_PID=$!
    fi
    _extract_dsh_token || true
    sleep 2
done
