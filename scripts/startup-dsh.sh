#!/bin/bash -e
# astroai/dsh: dsh (Agent tab), ghostty-web (Terminal tab), JupyterLab and VS Code
# (started the first time their tab is opened) and `canfar lab web` (Lab tab)
# behind dsh-canfar-proxy.py on :5000. Routes and tabs: /opt/astroai/studio/tabs.json.

export ASTROAI_SESSION_KIND="${ASTROAI_SESSION_KIND:-dsh}"
export PATH="/opt/canfar/bin:/opt/astroai/bin:${PATH}"
export ASTROAI_STUDIO_PORT="${ASTROAI_STUDIO_PORT:-5000}"
export DSH_PORT="${DSH_PORT:-3080}"
export CANFAR_LAB_WEB_PORT="${CANFAR_LAB_WEB_PORT:-4792}"
export ASTROAI_TERMINAL_PORT="${ASTROAI_TERMINAL_PORT:-4793}"
export ASTROAI_JUPYTER_PORT="${ASTROAI_JUPYTER_PORT:-8888}"
export ASTROAI_VSCODE_PORT="${ASTROAI_VSCODE_PORT:-8080}"
export ASTROAI_TAB_TITLE="${ASTROAI_TAB_TITLE:-AstroAI}"

# Per-session state on scratch (dies with the session; Lab snapshots carry it to /arc).
_user="${USER:-${LOGNAME:-$(id -un 2>/dev/null || echo user)}}"
for _root in "${SCRATCH:-}" /scratch "${TMPDIR:-/tmp}"; do
    [[ -n "${_root}" && -d "${_root}" && -w "${_root}" ]] && break
done
_dsh_state="${_root}/.dsh-${_user}"
mkdir -p "${_dsh_state}/logs" "${_dsh_state}/tmp" "${_dsh_state}/pnpm-store" "${_dsh_state}/pnpm-home" \
    "${_dsh_state}/tools"
# The proxy drops tools/<id> when a tab with "start" is first opened; the loop
# below starts it. The Terminal is always on.
touch "${_dsh_state}/tools/terminal"
export ASTROAI_STUDIO_STATE="${_dsh_state}"
export npm_config_store_dir="${_dsh_state}/pnpm-store" PNPM_HOME="${_dsh_state}/pnpm-home"
export TMPDIR="${_dsh_state}/tmp"
export ASTROAI_DSH_TOKEN_FILE="${_dsh_state}/dsh-web-token"
_dsh_log="${_dsh_state}/logs/dsh.log"
_lab_log="${_dsh_state}/logs/lab-web.log"
rm -f "${ASTROAI_DSH_TOKEN_FILE}"
: >"${_dsh_log}"

# One bearer per session: the proxy sends it to the Lab, nothing else sees it.
CANFAR_LAB_WEB_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export CANFAR_LAB_WEB_TOKEN

# Bind :5000 before common-init: a slow /arc walk must not fail Skaha liveness.
_start_proxy() {
    python3 /opt/astroai/lib/dsh-canfar-proxy.py >>"${_dsh_state}/logs/proxy.log" 2>&1 &
    PROXY_PID=$!
}
_start_proxy

# shellcheck disable=SC1091
source /cadc/common-init.sh
astroai_boot_log "dsh: proxy :${ASTROAI_STUDIO_PORT} pid=${PROXY_PID}, state=${_dsh_state}"

export SRCDIR="${SRCDIR:-${WORK:-${SCRATCH:-/scratch}/src}}"
export WORK="${WORK:-${SRCDIR}}"
mkdir -p "${SRCDIR}" "${HOME}/.dsh"

cleanup() {
    astroai_boot_log "dsh: session exit rc=$?"
    kill "${PROXY_PID:-}" "${DSH_PID:-}" "${LAB_PID:-}" "${TOOL_PID[@]}" 2>/dev/null || true
    wait "${PROXY_PID:-}" "${DSH_PID:-}" "${LAB_PID:-}" "${TOOL_PID[@]}" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 143' TERM  # exit runs the EXIT trap; returning would re-enter the loop
trap 'exit 130' INT

# dsh profile `astroai`: MCP, presets and the vendored AstroAI plugins. No pnpm fetch.
canfar-lab --yes studio --prepare --profile canfar --no-install --no-team \
    >>"${_dsh_state}/logs/studio-prepare.log" 2>&1 \
    || astroai_boot_log "WARN: studio --prepare failed, see ${_dsh_state}/logs/studio-prepare.log"

_trust=()
# shellcheck disable=SC2086  # split the comma list into words
for _h in ws-uv.canfar.net ws-uvic.canfar.net staging.canfar.net workloads.canfar.net \
    workload-uv.canfar.net ${ASTROAI_STUDIO_TRUSTED_HOST//,/ } "$(hostname -f 2>/dev/null || hostname)"; do
    [[ -n "${_h}" ]] && _trust+=(--trusted-host "${_h}")
done

_start_dsh() {
    if [[ -n "${DSH_PID:-}" ]]; then kill "${DSH_PID}" 2>/dev/null || true; fi
    rm -f "${ASTROAI_DSH_TOKEN_FILE}"
    # dsh reads keys from ~/.dsh/.credentials.yaml; inherited env would shadow it.
    (cd "${SRCDIR}" && exec env -u CANFAR_LAB_WEB_TOKEN -u OPENCODE_API_KEY -u DEEPSEEK_API_KEY \
        -u GEMINI_API_KEY -u OPENAI_API_KEY -u ANTHROPIC_API_KEY \
        stdbuf -oL -eL dsh --profile astroai --no-open --port "${DSH_PORT}" "${_trust[@]}") \
        >>"${_dsh_log}" 2>&1 &
    DSH_PID=$!
    _token_logged=""
    astroai_boot_log "dsh: dsh pid=${DSH_PID} on :${DSH_PORT}"
}

# Lab tab. --restore reinstalls last session's agents; --restore-code brings the
# latest snapshot back when $SCRATCH/src is empty. The checklist shows progress.
_start_lab() {
    (cd "${SRCDIR}" && exec canfar-lab web --port "${CANFAR_LAB_WEB_PORT}" \
        --token-file "${_dsh_state}/lab-web.token" --restore --restore-code) >>"${_lab_log}" 2>&1 &
    LAB_PID=$!
    astroai_boot_log "dsh: canfar lab web pid=${LAB_PID} on :${CANFAR_LAB_WEB_PORT}"
}

# On-demand tools. Each serves on loopback; the proxy routes its tab.
_base="${skaha_sessionid:+/session/contrib/${skaha_sessionid}}"
declare -A TOOL_PID=() TOOL_STARTED=() TOOL_FAILS=()
_start_tool() {
    local name="$1" log="${_dsh_state}/logs/$1.log"
    case "${name}" in
        terminal)
            (cd "${SRCDIR}" && HOST=127.0.0.1 PORT="${ASTROAI_TERMINAL_PORT}" PWD="${SRCDIR}" \
                ASTROAI_TAB_TITLE="${ASTROAI_TAB_TITLE}" exec node /opt/ghostty-web/server.mjs) >>"${log}" 2>&1 &
            ;;
        jupyter)
            # Image config from /etc/jupyter; workspaces and state need a writable dir.
            mkdir -p "${_dsh_state}/jupyter-config" "${_dsh_state}/jupyter-runtime" "${_dsh_state}/jupyter-data"
            (cd "${SRCDIR}" && JUPYTER_CONFIG_DIR="${_dsh_state}/jupyter-config" \
                JUPYTER_RUNTIME_DIR="${_dsh_state}/jupyter-runtime" \
                JUPYTER_DATA_DIR="${_dsh_state}/jupyter-data" exec jupyter lab --ip 127.0.0.1 \
                --config /etc/jupyter/jupyter_server_config.py \
                --port "${ASTROAI_JUPYTER_PORT}" --no-browser --ServerApp.token='' \
                --ServerApp.password='' --ServerApp.allow_remote_access=True \
                --ServerApp.base_url="${_base}/jupyter/" --ServerApp.root_dir=/ \
                --ServerApp.preferred_dir="${SRCDIR}" --ServerApp.log_level=WARN) >>"${log}" 2>&1 &
            ;;
        vscode)
            mkdir -p "${_dsh_state}/vscode-data/Machine" "${_dsh_state}/vscode-extensions"
            cp -f /opt/openvscode-server/data/Machine/settings.json \
                "${_dsh_state}/vscode-data/Machine/settings.json" 2>/dev/null || true
            openvscode-server --host 127.0.0.1 --port "${ASTROAI_VSCODE_PORT}" \
                --without-connection-token --server-base-path "${_base}/vscode" \
                --user-data-dir "${_dsh_state}/vscode-data" \
                --extensions-dir "${_dsh_state}/vscode-extensions" \
                --default-folder "${SRCDIR}" >>"${log}" 2>&1 &
            ;;
        *) return 1 ;;
    esac
    TOOL_PID[${name}]=$!
    TOOL_STARTED[${name}]=${SECONDS}
    astroai_boot_log "dsh: ${name} pid=${TOOL_PID[${name}]}"
}

_supervise_tools() {
    local req name pid
    for req in "${_dsh_state}"/tools/*; do
        [[ -e "${req}" ]] || continue
        name="${req##*/}"
        pid="${TOOL_PID[${name}]:-}"
        [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null && continue
        if [[ -n "${pid}" ]] && (( SECONDS - ${TOOL_STARTED[${name}]:-0} < 15 )); then
            TOOL_FAILS[${name}]=$(( ${TOOL_FAILS[${name}]:-0} + 1 ))
        fi
        if (( ${TOOL_FAILS[${name}]:-0} >= 3 )); then
            # Crashing on start: stop retrying until the tab is opened again.
            astroai_boot_log "dsh: ${name} keeps exiting, see ${_dsh_state}/logs/${name}.log"
            rm -f "${req}"
            unset "TOOL_PID[${name}]"
            TOOL_FAILS[${name}]=0
            continue
        fi
        _start_tool "${name}" || rm -f "${req}"
    done
}

_capture_dsh_token() {
    local tok
    tok="$(grep -aoE '[?&]token=[A-Za-z0-9_-]+' "${_dsh_log}" 2>/dev/null | tail -1 | cut -d= -f2-)"
    [[ -n "${tok}" ]] || return 1
    printf '%s\n' "${tok}" >"${ASTROAI_DSH_TOKEN_FILE}"
}

_start_dsh
_start_lab
astroai_boot_log "dsh: services started, supervising"

while true; do
    kill -0 "${PROXY_PID}" 2>/dev/null || { astroai_boot_log "dsh: proxy died, restarting"; _start_proxy; }
    if ! kill -0 "${DSH_PID}" 2>/dev/null; then
        astroai_boot_log "dsh: dsh died, restarting (tail ${_dsh_log})"
        tail -n 20 "${_dsh_log}" | sed 's/^/  dsh: /' >&2 || true
        _start_dsh
    fi
    kill -0 "${LAB_PID}" 2>/dev/null || { astroai_boot_log "dsh: lab web died, restarting"; _start_lab; }
    _supervise_tools
    if [[ -z "${_token_logged}" ]] && _capture_dsh_token; then
        astroai_boot_log "dsh web token captured for Skaha Connect redirect"
        _token_logged=1
    fi
    sleep 2
done
