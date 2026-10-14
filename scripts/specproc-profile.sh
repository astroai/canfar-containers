# AstroAI specproc PATH and spectrum-stack variables.
# Bash-only (/etc/profile sources profile.d for login shells, including sh).
if [ -z "${BASH_VERSION:-}" ]; then
    return 0 2>/dev/null || exit 0
fi

_specproc_prepend() {
    local p="$1"
    [[ -n "${p}" && -d "${p}" ]] || return 0
    case ":${PATH}:" in
        *":${p}:"*) ;;
        *) export PATH="${p}:${PATH}" ;;
    esac
}

_specproc_prepend /opt/astroai/venv/specproc/bin
_specproc_prepend /opt/astroai/julia/bin
unset -f _specproc_prepend

export SPS_HOME="${SPS_HOME:-/opt/astroai/fsps}"
export JULIA_PROJECT="${JULIA_PROJECT:-/opt/astroai/julia/korg}"
export TURBOSPECTRUM_ROOT="${TURBOSPECTRUM_ROOT:-/opt/astroai/Turbospectrum_NLTE}"
export TSFITPY_ROOT="${TSFITPY_ROOT:-/opt/astroai/TSFitPy}"
export SPECPROC_DATA="${SPECPROC_DATA:-/specproc-data}"
export ARIADNE_MODELS="${ARIADNE_MODELS:-${SPECPROC_DATA}/models/ariadne/spectra}"
export ISOCHRONES="${ISOCHRONES:-${SPECPROC_DATA}/isochrones}"
export DUSTMAPS_CONFIG_FNAME="${DUSTMAPS_CONFIG_FNAME:-/opt/astroai/share/specproc/dustmapsrc}"

# Korg's precompile cache is not in the image. Write it on scratch when that
# disk exists, otherwise under /tmp. The image depot stays on the path so the
# installed package and its artifact links are still found.
_julia_write="${SCRATCH:-/tmp}/julia-compile"
mkdir -p "${_julia_write}" 2>/dev/null || true
export JULIA_DEPOT_PATH="${_julia_write}:/opt/astroai/julia/depot"
unset _julia_write

# pykurucz is a repo checkout, not an installed distribution.
case ":${PYTHONPATH:-}:" in
    *":/opt/astroai/pykurucz:"*) ;;
    *) export PYTHONPATH="/opt/astroai/pykurucz${PYTHONPATH:+:${PYTHONPATH}}" ;;
esac

# pymoog reads $HOME/.pymoog. The image keeps that tree under /opt so it
# does not land in the home quota. Skip when the user already has one.
if [[ -d /opt/astroai/pymoog-home/.pymoog && -n "${HOME:-}" && ! -e "${HOME}/.pymoog" ]]; then
    ln -s /opt/astroai/pymoog-home/.pymoog "${HOME}/.pymoog" 2>/dev/null || true
fi
