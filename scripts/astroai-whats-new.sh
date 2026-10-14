# Show /opt/astroai/whats-new.txt once per note id, on an interactive terminal.
# The stamp lives under ~/.astroai/lab (persistent /arc/home on CANFAR).
# Non-interactive shells, including session startup, do not print or stamp,
# so the first real shell still shows the note.
# Silence with ASTROAI_WHATS_NEW=0.

astroai_show_whats_new() {
    case "${ASTROAI_WHATS_NEW:-}" in
        0|false|no|off) return 0 ;;
    esac
    case $- in
        *i*) ;;
        *) return 0 ;;
    esac
    [[ -t 1 ]] || return 0
    [[ -n "${HOME:-}" ]] || return 0

    local _file _id _state _stamp _seen
    _file="${ASTROAI_WHATS_NEW_FILE:-/opt/astroai/whats-new.txt}"
    [[ -r "${_file}" ]] || return 0
    _id="$(sed -n '1s/^# id:[[:space:]]*//p' "${_file}")"
    [[ -n "${_id}" ]] || return 0

    _state="${CANFAR_LAB_CONFIG_DIR:-${HOME}/.astroai/lab}"
    _stamp="${_state}/whats-new-seen"
    _seen=""
    if [[ -f "${_stamp}" ]]; then
        _seen="$(tr -d '[:space:]' < "${_stamp}" 2>/dev/null || true)"
    fi
    [[ "${_seen}" == "${_id}" ]] && return 0

    echo
    sed '1{/^# id:/d;}' "${_file}"
    echo

    mkdir -p "${_state}" 2>/dev/null || return 0
    printf '%s\n' "${_id}" > "${_stamp}" 2>/dev/null || true
}

astroai_show_whats_new
