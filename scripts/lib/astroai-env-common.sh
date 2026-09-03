# Session path + quota helpers for AstroAI startup scripts.
# Env save/resume/workspace logic lives in astroai — do not duplicate here.

ASTROAI_ENV_COMMON_LOADED=1
set -o pipefail 2>/dev/null || true

if [[ -f /opt/astroai/lib/astroai-ui.sh ]]; then
    # shellcheck disable=SC1091
    source /opt/astroai/lib/astroai-ui.sh
elif [[ -f "${BASH_SOURCE[0]%/*}/astroai-ui.sh" ]]; then
    # shellcheck disable=SC1091
    source "${BASH_SOURCE[0]%/*}/astroai-ui.sh"
fi

# Stderr → `canfar logs`; also ~/.astroai/lab/boot.log on shared home (survives
# pod death). Keep lines short — Skaha truncates very large session logs.
astroai_boot_log() {
    local ts sid kind line dir
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || printf '?')"
    sid="${skaha_sessionid:-${SKAHA_SESSIONID:-?}}"
    kind="${ASTROAI_SESSION_KIND:-?}"
    line="${ts} sid=${sid} pid=$$ kind=${kind} $*"
    echo "[astroai-boot] ${line}" >&2 || true
    dir="${ASTROAI_LAB_CONFIG_DIR:-${HOME}/.astroai/lab}"
    mkdir -p "${dir}" 2>/dev/null || return 0
    echo "${line}" >> "${dir}/boot.log" 2>/dev/null || true
}

# Browser tab: Skaha sets the pod hostname to the session name (lowercase).
# Docker-id / localhost hostnames keep the per-app fallback.
astroai_session_title() {
    local fallback="${1:-AstroAI}"
    local name
    name="$(hostname -s 2>/dev/null || hostname 2>/dev/null || true)"
    name="${name%%.*}"
    local lower="${name,,}"
    case "${lower}" in
        "" | localhost | astroai | unknown)
            echo "${fallback}"
            return
            ;;
    esac
    if [[ "${lower}" =~ ^[0-9a-f]{12,}$ ]]; then
        echo "${fallback}"
        return
    fi
    echo "${name}"
}

# Runtime paths — WORK / SCRATCH (canonical; Skaha bridge in astroai-profile.sh
# maps TMP_* → WORK/SCRATCH). Defaults come from image ENV only.
astroai_default_src_dir() {
    echo "${WORK:-${SCRATCH:-/scratch}/src}"
}

astroai_default_scratch_dir() {
    echo "${SCRATCH:-/scratch}"
}

astroai_scratch_dir() {
    echo "${SCRATCH:-$(astroai_default_scratch_dir)}"
}

astroai_scratch_available() {
    local _scratch
    _scratch="$(astroai_scratch_dir)"
    [[ -d "${_scratch}" && -w "${_scratch}" ]]
}

# Code/env root: WORK when set, else default src dir if writable, else scratch, else HOME.
astroai_src_dir() {
    if [[ -n "${WORK:-}" ]]; then
        echo "${WORK}"
        return
    fi
    local _default_src
    _default_src="$(astroai_default_src_dir)"
    if [[ -d "${_default_src}" && -w "${_default_src}" ]]; then
        echo "${_default_src}"
    elif astroai_scratch_available; then
        echo "$(astroai_scratch_dir)"
    else
        echo "${HOME}"
    fi
}

# Echo integer 0-100 used percentage for path, or empty if unknown.
# Prefer Ceph directory quota xattrs. df on /arc/home is the shared pool,
# not the user's quota — never use it for home.
astroai_quota_used_pct() {
    local path="${1:-}"
    [[ -d "${path}" ]] || return 0
    if command -v getfattr >/dev/null 2>&1; then
        local _cur="${path}" _max _used _i
        for _i in 1 2 3 4 5 6 7 8; do
            _max="$(getfattr --only-values -n ceph.quota.max_bytes "${_cur}" 2>/dev/null || true)"
            _max="${_max//[!0-9]/}"
            if [[ -n "${_max}" && "${_max}" -gt 0 ]]; then
                _used="$(getfattr --only-values -n ceph.dir.rbytes "${_cur}" 2>/dev/null || true)"
                _used="${_used//[!0-9]/}"
                if [[ -n "${_used}" ]]; then
                    awk -v u="${_used}" -v t="${_max}" 'BEGIN {
                        if (t>0) printf "%.0f", (u/t)*100
                        else print 0
                    }'
                    return 0
                fi
                break
            fi
            [[ "${_cur}" == "/" ]] && break
            _parent="$(dirname "${_cur}")"
            # Quotas live on the user home or project dir, not /arc or /arc/home.
            if [[ "${_parent}" == /arc/home || "${_parent}" == /arc/projects || "${_parent}" == /arc ]]; then
                break
            fi
            _cur="${_parent}"
        done
    fi
    local resolved
    resolved="$(readlink -f "${path}" 2>/dev/null || echo "${path}")"
    if [[ "${resolved}" == /arc/home || "${resolved}" == /arc/home/* ]]; then
        return 0
    fi
    df "${path}" 2>/dev/null | awk 'NR>1 {
        pct=$5; gsub(/%/, "", pct); print pct
    }'
}

# Echo /arc/projects/<name> when start path is inside a project, else empty.
astroai_find_arc_project_root() {
    local start="${1:-${PWD}}"
    local proj_path="${start}"

    [[ -d /arc/projects ]] || return 0
    while [[ "${proj_path}" != "/" && "${proj_path}" != "/arc/projects" ]]; do
        local parent
        parent="$(dirname "${proj_path}")"
        if [[ "${parent}" == /arc/projects ]]; then
            echo "${proj_path}"
            return 0
        fi
        proj_path="${parent}"
    done
}

# Check storage quota for a path. Prints warnings at thresholds.
# Returns: 0 = OK, 1 = warning (>80%), 2 = critical (>95%)
astroai_check_quota() {
    local path="${1:-}"
    local label="${2:-$(basename "${path}")}"

    [[ -d "${path}" ]] || return 0

    local used_pct
    used_pct="$(astroai_quota_used_pct "${path}")"
    [[ -n "${used_pct}" ]] || return 0

    if [[ "${used_pct}" -ge 95 ]]; then
        astroai_warn "  ⚠  ${label}: ${used_pct}% used — CRITICAL (near quota limit)"
        return 2
    elif [[ "${used_pct}" -ge 90 ]]; then
        astroai_warn "  ⚠  ${label}: ${used_pct}% used — prune caches soon (check astroai status)"
        return 1
    elif [[ "${used_pct}" -ge 80 ]]; then
        astroai_warn "  ⚠  ${label}: ${used_pct}% used — monitor (astroai status)"
        return 1
    fi
    return 0
}

# Print a one-line quota summary for a path.
astroai_quota_line() {
    local path="${1:-}"
    local label="${2:-$(basename "${path}")}"

    [[ -d "${path}" ]] || { echo "  ${label}: not mounted"; return; }

    df -h "${path}" 2>/dev/null | awk -v lbl="${label}" 'NR>1 {
        pct=$5; gsub(/%/, "", pct);
        if (pct >= 95) alert=" ⚠ CRITICAL";
        else if (pct >= 90) alert=" ⚠ high";
        else if (pct >= 80) alert=" ⚠ monitor";
        else alert="";
        printf "  %-8s %s / %s (%s%%)%s\n", lbl, $3, $2, $5, alert
    }'
}

# Run quota warnings for relevant paths at session start.
# Skip when stderr is not a TTY (CANFAR session logs capture startup stderr).
astroai_quota_startup_check() {
    if [[ ! -t 2 ]]; then
        return 0
    fi

    local warned=0

    if [[ -d "${HOME}" ]]; then
        astroai_check_quota "${HOME}" "home (/arc/home/${USER})" || warned=1
    fi

    local proj_path
    proj_path="$(astroai_find_arc_project_root)"
    if [[ -n "${proj_path}" ]]; then
        local proj_label="project ($(basename "${proj_path}"))"
        astroai_check_quota "${proj_path}" "${proj_label}" || warned=1
    fi

    if [[ "${warned}" -eq 1 ]]; then
        echo ""
    fi
    return 0
}
