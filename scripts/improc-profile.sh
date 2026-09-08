# AstroAI improc PATH — science venv only.
# Conda CLIs (sourcextractor++, sky) are linked into /usr/local/bin; do not put
# conda env bins on PATH (their pythons would shadow GalSim/torch/TF).
# Bash-only (/etc/profile sources profile.d for all login shells, including sh).
if [ -z "${BASH_VERSION:-}" ]; then
    return 0 2>/dev/null || exit 0
fi

_improc_prepend() {
    local p="$1"
    [[ -n "${p}" && -d "${p}" ]] || return 0
    case ":${PATH}:" in
        *":${p}:"*) ;;
        *) export PATH="${p}:${PATH}" ;;
    esac
}

_improc_prepend /opt/astroai/venv/improc/bin
unset -f _improc_prepend
