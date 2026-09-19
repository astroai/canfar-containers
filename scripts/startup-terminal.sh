#!/bin/bash -e
# Browser terminal: ghostty-web + tmux on port 5000 (CANFAR Contributed).
# Harbor / portal name: `terminal` (astroai/terminal).

export ASTROAI_SESSION_KIND=terminal

source /cadc/common-init.sh

# Contributed ingress strips /session/contrib/<id>; serve at /.
export PORT=5000
export HOST=0.0.0.0
export ASTROAI_TAB_TITLE="$(astroai_session_title "AstroAI Terminal")"

astroai_boot_log "exec ghostty-web-server"
exec node /opt/ghostty-web/server.mjs
