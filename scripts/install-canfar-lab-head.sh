#!/bin/bash
# Reinstall canfar-lab from the default branch.
# The lockfile install that precedes this pins the rest of the environment.
# The Dockerfile ADDs the branch ref so this layer rebuilds when main moves.
set -euo pipefail
venv="${1:?venv path}"
test -s /tmp/canfar-lab-head.json
uv pip install --python "$venv" --upgrade \
    "canfar-lab @ git+https://github.com/astroai/canfar-lab.git"
chmod -R a+rwX "$venv"
uv cache clean
