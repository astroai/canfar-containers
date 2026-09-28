#!/bin/bash
# openscience on CANFAR: saved model keys, pinned binary, astronomy Python first
# on PATH, and one live session per data dir.
#
# ~/.openscience (sessions, artifacts, experiments) holds SQLite databases on
# /arc, which is shared by every session of the user. Two servers writing the
# same databases over CephFS corrupt them, so a lease file names the session
# that owns the dir; another live session falls back to per-session scratch.

set -u

OPENSCIENCE_BIN="${OPENSCIENCE_BIN:-/opt/openscience/bin/openscience}"
SCIENCE_VENV="${ASTROAI_SCIENCE_VENV:-/opt/astroai/venv/science}"
LEASE_NAME=".canfar-lease"
LEASE_TTL="${ASTROAI_OPENSCIENCE_LEASE_TTL:-180}"
HEARTBEAT="${ASTROAI_OPENSCIENCE_HEARTBEAT:-60}"

if [[ -r "${HOME}/.astroai/lab/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${HOME}/.astroai/lab/.env"
    set +a
fi

export OPENSCIENCE_DISABLE_AUTOUPDATE=1
export OPENSCIENCE_SKIP_ONBOARDING=1
# The image provides the Python stack; skip the micromamba download into ~/.openscience.
export OPENSCIENCE_SKIP_ENVIRONMENT_BOOTSTRAP=1
if [[ -x "${SCIENCE_VENV}/bin/python3" ]]; then
    export PATH="${SCIENCE_VENV}/bin:${PATH}"
fi
export PATH="${PATH}:/opt/astroai/venv/cadc/bin"

owner="${skaha_sessionid:-$(hostname 2>/dev/null || echo local)}"

# Lease line: "<session> <pid>". Only the session matters for ownership; the
# pid lets a short `openscience run` leave a long-lived `serve` lease alone.
lease_file() { printf '%s/%s' "$1" "${LEASE_NAME}"; }

lease_holder() {
    # Prints the session holding a fresh lease in $1, or nothing.
    local file age
    file=$(lease_file "$1")
    [[ -f "${file}" ]] || return 0
    age=$(($(date +%s) - $(stat -c %Y "${file}" 2>/dev/null || echo 0)))
    ((age < LEASE_TTL)) || return 0
    read -r who _ <"${file}" 2>/dev/null
    printf '%s' "${who:-}"
}

lease_dir=""
if [[ -z "${OPENSCIENCE_DATA_DIR:-}" && -d "${HOME}" ]]; then
    home_dir="${HOME}/.openscience"
    mkdir -p "${home_dir}" 2>/dev/null || true
    holder=$(lease_holder "${home_dir}")
    # The session proxy shows a notice while this flag names the other session.
    scratch_flag="${ASTROAI_OPENSCIENCE_STATE:+${ASTROAI_OPENSCIENCE_STATE}/history-on-scratch}"
    if [[ -z "${holder}" || "${holder}" == "${owner}" ]]; then
        lease_dir="${home_dir}"
        [[ -n "${scratch_flag}" ]] && rm -f "${scratch_flag}"
    else
        fallback="${SCRATCH:-/scratch}/.openscience"
        mkdir -p "${fallback}"
        export OPENSCIENCE_DATA_DIR="${fallback}"
        [[ -n "${scratch_flag}" ]] && printf '%s\n' "${holder}" >"${scratch_flag}"
        echo "openscience: ${home_dir} is in use by session ${holder}; this session keeps" \
            "its OpenScience history on scratch (${fallback}), which is deleted when" \
            "the session ends." >&2
    fi
fi

if [[ -z "${lease_dir}" ]]; then
    exec "${OPENSCIENCE_BIN}" "$@"
fi

# OpenScience locks and process ledgers name pids and treat a live pid as a
# live holder. Pids from another pod (or container) mean nothing here and may
# be reused, so a lock left by a killed session would block this one. The
# lease makes this session the only user of the dir, so clear that state when
# the dir was last used from a different pid namespace.
pidns="$(hostname 2>/dev/null)/$(readlink "/proc/$$/ns/pid" 2>/dev/null)"
clear_foreign_locks() {
    local host_file="$1/.canfar-pidns" last=""
    [[ -f "${host_file}" ]] && read -r last <"${host_file}"
    [[ "${last}" == "${pidns}" ]] && return 0
    find "$1" \( -type d -name '*.lock.coord' -prune -o -type f -name '*.lock' \) \
        -exec rm -rf {} + 2>/dev/null
    rm -f "$1/authority-processes.json" "$1/credential-processes.json"
    local config="${XDG_CONFIG_HOME:-${HOME}/.config}/openscience"
    rm -rf "${config}/data-root-switch.lock" "${config}/data-root-switch.intent" \
        "${config}/data-root-operations" "${config}"/*.lock.coord 2>/dev/null
    printf '%s\n' "${pidns}" >"${host_file}"
}

lease=$(lease_file "${lease_dir}")
claim() {
    # Take the lease when it is absent or stale; refresh it when this session holds it.
    local holder
    holder=$(lease_holder "${lease_dir}")
    if [[ -z "${holder}" ]]; then
        printf '%s %s\n' "${owner}" "$$" >"${lease}"
    elif [[ "${holder}" == "${owner}" ]]; then
        touch "${lease}"
    else
        return 1
    fi
}
claim && clear_foreign_locks "${lease_dir}"
(
    while kill -0 "$$" 2>/dev/null; do
        sleep "${HEARTBEAT}"
        claim || exit 0
    done
) &
heartbeat=$!

release() {
    kill "${heartbeat}" 2>/dev/null
    local who pid
    read -r who pid <"${lease}" 2>/dev/null
    if [[ "${who:-}" == "${owner}" && "${pid:-}" == "$$" ]]; then
        rm -f "${lease}"
    fi
}
trap release EXIT

# Without job control a background command reads /dev/null; keep the caller's stdin.
"${OPENSCIENCE_BIN}" "$@" <&0 &
child=$!
trap 'kill -TERM "${child}" 2>/dev/null; wait "${child}"; exit 143' TERM
trap 'wait "${child}"; exit 130' INT
wait "${child}"
