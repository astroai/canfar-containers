#!/usr/bin/bash
# Smoke test: astroai status team project + quotas in astroai/base.
set -euo pipefail

REGISTRY="${REGISTRY:-images.canfar.net}"
OWNER="${OWNER:-astroai}"
TAG="${BUILD_TAG:-local}"
IMAGE="${REGISTRY}/${OWNER}/base:${TAG}"

FAKE_HOME="$(mktemp -d)"
FAKE_ARC="$(mktemp -d)"
FAKE_SRC="$(mktemp -d)"
FAKE_SCRATCH="$(mktemp -d)"
trap 'rm -rf "${FAKE_HOME}" "${FAKE_ARC}" "${FAKE_SRC}" "${FAKE_SCRATCH}"' EXIT

mkdir -p "${FAKE_HOME}" "${FAKE_ARC}/projects/mygroup/data" "${FAKE_SRC}" "${FAKE_SCRATCH}"
chmod -R a+rwX "${FAKE_HOME}" "${FAKE_ARC}" "${FAKE_SRC}" "${FAKE_SCRATCH}"

echo "astroai status arc project test (${IMAGE})"
echo "=============================================="

docker run --rm \
    -u "$(id -u):$(id -g)" \
    -e HOME="${FAKE_HOME}" \
    -e USER=testuser \
    -v "${FAKE_HOME}:${FAKE_HOME}" \
    -v "${FAKE_ARC}/projects:/arc/projects" \
    -v "${FAKE_SRC}:/srcdir" \
    -v "${FAKE_SCRATCH}:/scratch" \
    "${IMAGE}" \
    bash -lc '
set -e
source /etc/profile.d/astroai.sh
cd /arc/projects/mygroup
astroai status --json | python3 -c "
import json, sys
d = json.load(sys.stdin)
ap = d.get(\"arc_project\")
assert ap and ap[\"name\"] == \"mygroup\", ap
assert ap[\"is_cwd\"] is True, ap
assert ap.get(\"access\") in (\"rw\", \"ro\", \"x\", \"none\"), ap
assert \"acl_groups\" in ap, ap
assert ap[\"quota\"] and ap[\"quota\"].get(\"free\"), ap
assert \"gms_groups\" in d
assert \"vault\" in d
assert any(q[\"label\"] == \"mygroup\" for q in d[\"quotas\"]), d[\"quotas\"]
print(\"STATUS_JSON_OK\")
"
astroai status 2>&1 | grep -F "Team project (cwd): /arc/projects/mygroup"
upgrade-cadc-tools.sh list | grep -F canfar-lab
test -w /opt/astroai/venv/cadc
echo STATUS_HUMAN_OK
'

echo "astroai status arc project test passed."
