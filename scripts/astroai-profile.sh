# AstroAI image shell hook — platform PATH + user CLI dirs.
# Session paths, caches, and hooks live in astroai-lab (/etc/astroai-lab/profile.sh).
#
# Bash-only (/etc/profile sources profile.d for all login shells, including sh).
if [ -z "${BASH_VERSION:-}" ]; then
    return 0 2>/dev/null || exit 0
fi

# Source dir: SRCDIR (user) > Skaha TMP_SRC_DIR > WORK > /srcdir.
# WORK is kept as an alias of SRCDIR. astroai env export then relocates
# both to $SCRATCH/src when /srcdir is the container overlay (wiped on
# OOM restart) and /scratch is a real volume.
export SRCDIR="${SRCDIR:-${TMP_SRC_DIR:-${WORK:-/srcdir}}}"
export WORK="${SRCDIR}"
export SCRATCH="${TMP_SCRATCH_DIR:-${SCRATCH:-/scratch}}"

# CADC venv first — astroai env export runs from profile.sh below.
case ":${PATH}:" in
    *":/opt/astroai/venv/cadc/bin:"*) ;;
    *) export PATH="/opt/astroai/venv/cadc/bin:/opt/astroai/bin:${PATH}" ;;
esac

[[ -f /etc/astroai-lab/profile.sh ]] && source /etc/astroai-lab/profile.sh

# Team + user CLI installs (ASTROAI_LAB_BIN_DIR) ahead of platform paths.
if [[ -n "${ASTROAI_LAB_PATH_PREFIX:-}" ]]; then
    IFS=':' read -ra _canfar_lab_path_parts <<< "${ASTROAI_LAB_PATH_PREFIX}"
    _canfar_lab_i=""
    for ((_canfar_lab_i=${#_canfar_lab_path_parts[@]}-1; _canfar_lab_i>=0; _canfar_lab_i--)); do
        _canfar_lab_p="${_canfar_lab_path_parts[_canfar_lab_i]}"
        [[ -n "${_canfar_lab_p}" ]] || continue
        case ":${PATH}:" in
            *":${_canfar_lab_p}:"*) ;;
            *) export PATH="${_canfar_lab_p}:${PATH}" ;;
        esac
    done
    unset _canfar_lab_p _canfar_lab_i _canfar_lab_path_parts
fi

use-project() {
    local target="${1:-}"
    local proj_dir=""
    if [[ -z "${target}" ]]; then
        target="$(pwd)"
    fi
    if [[ -d "${WORK:-/srcdir}/${target}" ]]; then
        proj_dir="${WORK:-/srcdir}/${target}"
    elif [[ -d "${target}" ]]; then
        proj_dir="$(cd "${target}" 2>/dev/null && pwd)"
    fi
    if [[ -z "${proj_dir}" ]]; then
        echo "Project not found: ${target}" >&2
        return 1
    fi
    if [[ -d "${proj_dir}/.pixi/envs/default" ]]; then
        export VIRTUAL_ENV="${proj_dir}/.pixi/envs/default"
        export PATH="${proj_dir}/.pixi/envs/default/bin:${PATH}"
        echo "Activated pixi env: $(basename "${proj_dir}") (${VIRTUAL_ENV})"
    elif [[ -d "${proj_dir}/.venv" ]]; then
        export VIRTUAL_ENV="${proj_dir}/.venv"
        export PATH="${proj_dir}/.venv/bin:${PATH}"
        echo "Activated venv: $(basename "${proj_dir}") (${VIRTUAL_ENV})"
    else
        echo "No .pixi or .venv found under ${proj_dir} — run 'pixi install' or 'uv sync' first." >&2
        return 1
    fi
    export ASTROAI_PROJECT="${proj_dir}"
}
canfar-env() { use-project "$@"; }

