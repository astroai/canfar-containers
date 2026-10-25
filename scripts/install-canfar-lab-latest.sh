#!/bin/bash
# Reinstall the latest canfar-lab release from PyPI.
# The lockfile install that precedes this pins the rest of the environment.
# The Dockerfile ADDs the PyPI JSON so this layer rebuilds when a release is published.
set -euo pipefail
venv="${1:?venv path}"
test -s /tmp/canfar-lab-pypi.json
uv pip install --python "$venv" --upgrade canfar-lab
chmod -R a+rwX "$venv"
uv cache clean
