#!/bin/bash -e
# JupyterLab for CANFAR Notebook sessions (port 8888).
#
# NOTE: Stock science-platform launch-notebook.yaml runs /skaha-system/start-jupyterlab.sh
# instead of this script. This file is used only when the cluster overrides command to
# /skaha/startup.sh (see docs/OPERATORS.md).
#
# Skaha passes the session ID as the first argument to /skaha/startup.sh.
# The platform also sets JUPYTER_TOKEN to the same value.

export ASTROAI_SESSION_KIND="${ASTROAI_SESSION_KIND:-notebook}"
export ASTROAI_LAB_ENSURE_KERNEL="${ASTROAI_LAB_ENSURE_KERNEL:-1}"
source /cadc/common-init.sh

SESSION_ID="${1:-${JUPYTER_TOKEN:-}}"
PORT=8888

# Read image defaults from /etc/jupyter, but keep JUPYTER_CONFIG_DIR writable —
# jupyter_core migrate() tries to create $JUPYTER_CONFIG_DIR/migrated as the user.
export JUPYTER_CONFIG_DIR="${TMPDIR:-/tmp}/jupyter-config"
export JUPYTER_CONFIG_PATH=/etc/jupyter
export JUPYTER_RUNTIME_DIR="${TMPDIR:-/tmp}/jupyter-runtime"
export JUPYTER_DATA_DIR="${TMPDIR:-/tmp}/jupyter-data"
mkdir -p "${JUPYTER_CONFIG_DIR}" "${JUPYTER_RUNTIME_DIR}" "${JUPYTER_DATA_DIR}"

# ponytail: quarantine legacy ~/.jupyter keys every startup → drop when platform stops writing NotebookApp to /arc/home
if [[ -d "${HOME}/.jupyter" ]]; then
    _legacy_dir="${HOME}/.jupyter.astroai-legacy"
    shopt -s nullglob
    for _cfg in "${HOME}/.jupyter"/jupyter_{notebook,server,lab}_config.{py,json}; do
        if [[ -f "${_cfg}" ]] && grep -qE 'NotebookApp|c\.NotebookApp' "${_cfg}" 2>/dev/null; then
            mkdir -p "${_legacy_dir}"
            mv "${_cfg}" "${_legacy_dir}/$(basename "${_cfg}").$(date +%s)"
        fi
    done
fi

if [[ -n "${SESSION_ID}" ]]; then
    export JUPYTER_TOKEN="${SESSION_ID}"
fi

# Browser tab: Skaha pod hostname is the session name (see astroai_session_title).
if command -v python3 >/dev/null; then
    python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, '/opt/astroai/lib')
from session_title import write_jupyter_lab_page_config
write_jupyter_lab_page_config(Path('${JUPYTER_DATA_DIR}'), 'AstroAI Notebook')
" 2>/dev/null || true
fi

BASE_URL_ARGS=()
if [[ -n "${SESSION_ID}" ]]; then
    # Match platform start-jupyterlab.sh (no leading slash)
    BASE_URL_ARGS=(--ServerApp.base_url="session/notebook/${SESSION_ID}")
fi

ROOT_DIR="$(astroai_src_dir)"
if [[ ! -d "${ROOT_DIR}" ]]; then
    ROOT_DIR="${HOME}"
fi

astroai_boot_log "exec jupyter lab"
# Do not pass --LabApp.app_name: not a trait in JupyterLab 4 (fatal Bad config).
exec jupyter lab \
    --ip 0.0.0.0 \
    --port "${PORT}" \
    --no-browser \
    --config /etc/jupyter/jupyter_server_config.py \
    --ServerApp.log_level=ERROR \
    --ServerApp.root_dir="${ROOT_DIR}" \
    "${BASE_URL_ARGS[@]}"
