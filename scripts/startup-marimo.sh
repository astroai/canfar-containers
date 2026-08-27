#!/bin/bash -e
# Marimo reactive notebooks on port 5000.
# Seed starter.py once, then open it so new users land in the guide notebook
# (cwd remains WORK/notebooks for File > Open / symlinks).

export ASTROAI_SESSION_KIND="${ASTROAI_SESSION_KIND:-marimo}"
source /cadc/common-init.sh

# Shared OpenRouter key (and gh token hook) before marimo starts — same store
# agents use via ~/.astroai/lab/.env + agent-env.sh.
if [[ -f "${HOME}/.astroai/lab/agent-env.sh" ]]; then
    # shellcheck disable=SC1091
    source "${HOME}/.astroai/lab/agent-env.sh"
elif [[ -f "${HOME}/.astroai/lab/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${HOME}/.astroai/lab/.env"
    set +a
fi

# common-init cds to the session work root (WORK).
NOTEBOOKS_DIR="$(pwd)/notebooks"
mkdir -p "${NOTEBOOKS_DIR}"

STARTER_SRC="/opt/astroai/notebooks/starter.py"
STARTER_DST="${NOTEBOOKS_DIR}/starter.py"
# Seed once — never overwrite student edits.
if [[ -f "${STARTER_SRC}" && ! -e "${STARTER_DST}" ]]; then
    cp "${STARTER_SRC}" "${STARTER_DST}"
fi

# Convenience symlinks so File > Open and the file browser widget can reach
# session storage ($WORK, /scratch) and persistent storage (/arc) in one click.
ln -sfn /scratch "${NOTEBOOKS_DIR}/📁_scratch" 2>/dev/null || true
ln -sfn "${WORK:-${SCRATCH:-/scratch}/src}" "${NOTEBOOKS_DIR}/📁_work" 2>/dev/null || true
ln -sfn /arc "${NOTEBOOKS_DIR}/📁_arc" 2>/dev/null || true

cd "${NOTEBOOKS_DIR}"

# Ensure marimo AI config exists with OpenRouter API key (astroai agent setup marimo).
# Non-destructive: only creates/seeds ~/.marimo.toml on first launch; never overwrites.
# Also persists any discovered key into ~/.astroai/lab/.env for agent CLIs.
if command -v astroai >/dev/null 2>&1; then
    astroai --yes agent setup marimo 2>/dev/null || true
fi

# Prefer opening the starter notebook for a guided first screen. If the user
# already has other notebooks in this folder, open the directory home instead.
MARIMO_TARGET="."
if [[ -f "${STARTER_DST}" ]]; then
    other_notebooks=0
    for f in "${NOTEBOOKS_DIR}"/*.py; do
        [[ -e "${f}" ]] || continue
        [[ "$(basename "${f}")" == "starter.py" ]] && continue
        other_notebooks=$((other_notebooks + 1))
    done
    if [[ "${other_notebooks}" -eq 0 ]]; then
        MARIMO_TARGET="starter.py"
    fi
fi

# CANFAR contributed ingress strips /session/contrib/<id> before forwarding
# (same as webterm). Do not pass --base-url here — marimo would only serve under
# that prefix and the proxied request for / would 404.

astroai_boot_log "exec marimo"
exec marimo --log-level warn edit \
    --no-token \
    --port 5000 \
    --host 0.0.0.0 \
    --skip-update-check \
    --headless \
    "${MARIMO_TARGET}"
