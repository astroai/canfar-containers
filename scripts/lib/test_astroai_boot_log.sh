#!/usr/bin/env bash
# Unit check: astroai_boot_log writes stderr + boot.log.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/lib/astroai-env-common.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

export HOME="${TMP}"
export ASTROAI_LAB_CONFIG_DIR="${TMP}/.astroai/lab"
export ASTROAI_SESSION_KIND=testkind
export SKAHA_SESSIONID=sess-test-1

err="$(astroai_boot_log "unit-probe" 2>&1 >/dev/null)"
[[ "${err}" == *"[astroai-boot]"* ]] || {
    echo "FAIL: missing stderr prefix: ${err}" >&2
    exit 1
}
[[ "${err}" == *"kind=testkind"* ]] || {
    echo "FAIL: missing kind in stderr: ${err}" >&2
    exit 1
}
[[ "${err}" == *"sid=sess-test-1"* ]] || {
    echo "FAIL: missing sid in stderr: ${err}" >&2
    exit 1
}
[[ "${err}" == *"unit-probe"* ]] || {
    echo "FAIL: missing message in stderr: ${err}" >&2
    exit 1
}

boot="${ASTROAI_LAB_CONFIG_DIR}/boot.log"
[[ -f "${boot}" ]] || {
    echo "FAIL: boot.log not created" >&2
    exit 1
}
grep -q "unit-probe" "${boot}" || {
    echo "FAIL: boot.log missing probe line" >&2
    exit 1
}

echo "PASS: astroai_boot_log stderr + boot.log"
