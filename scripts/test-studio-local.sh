#!/bin/bash
# Local Studio boot regression (Skaha-shaped volumes + Connect URL path).
#
# Covers the stamp-skip failure mode: after agent-setup-stamp exists,
# dangling ~/.dsh/{sessions,storages} scratch links must be restored so dsh
# emits a web token (otherwise Connect 401s).
#
# Usage:
#   TAG=26.09 ./scripts/test-studio-local.sh
#   make test-studio-local BUILD_TAG=26.09
set -euo pipefail

OWNER="${OWNER:-astroai}"
REGISTRY="${REGISTRY:-images.canfar.net}"
TAG="${TAG:-${BUILD_TAG:-local}}"
IMAGE="${REGISTRY}/${OWNER}/studio:${TAG}"
HOST_PORT="${STUDIO_TEST_PORT:-15050}"

echo "Studio local boot regression against ${IMAGE}"

FAKE_HOME="$(mktemp -d -p "${TMPDIR:-/tmp}" studiohome.XXXX)"
FAKE_SCRATCH="$(mktemp -d -p "${TMPDIR:-/tmp}" studioscratch.XXXX)"
FAKE_SRC="$(mktemp -d -p "${TMPDIR:-/tmp}" studiosrc.XXXX)"
NAME="studio-local-test-$$"
cleanup() {
    docker rm -f "${NAME}" >/dev/null 2>&1 || true
    rm -rf "${FAKE_HOME}" "${FAKE_SCRATCH}" "${FAKE_SRC}"
}
trap cleanup EXIT

chmod -R a+rwX "${FAKE_HOME}" "${FAKE_SCRATCH}" "${FAKE_SRC}"
mkdir -p "${FAKE_HOME}/.dsh" "${FAKE_HOME}/.astroai/lab"
# Prior-session state: stamp present + dangling dsh scratch links.
ln -s /scratch/.cache-old/data/_dsh/sessions "${FAKE_HOME}/.dsh/sessions"
ln -s /scratch/.cache-old/data/_dsh/storages "${FAKE_HOME}/.dsh/storages"
date -u +%Y-%m-%dT%H:%M:%SZ >"${FAKE_HOME}/.astroai/lab/agent-setup-stamp"
echo 'agent-default-model: {}' >"${FAKE_HOME}/.dsh/settings.yaml"

SESSION_ID="studio-local-$$"
docker run --rm --name "${NAME}" -d \
    -u "$(id -u):$(id -g)" \
    -e HOME=/arc/home/testuser \
    -e USER=testuser \
    -e SCRATCH=/scratch \
    -e WORK=/srcdir \
    -e SRCDIR=/srcdir \
    -e skaha_sessionid="${SESSION_ID}" \
    -e ASTROAI_SESSION_KIND=studio \
    -e ASTROAI_LAB_WORK_ON_SCRATCH=0 \
    -p "${HOST_PORT}:5000" \
    -v "${FAKE_HOME}:/arc/home/testuser" \
    -v "${FAKE_SCRATCH}:/scratch" \
    -v "${FAKE_SRC}:/srcdir" \
    "${IMAGE}" >/dev/null

ready=0
code=000
for _ in $(seq 1 90); do
    if ! docker ps --filter "name=^/${NAME}$" -q | grep -q .; then
        echo "FAIL: container exited before ready" >&2
        docker logs "${NAME}" 2>&1 | tail -40 >&2 || true
        exit 1
    fi
    code="$(
        curl -sS -o /dev/null -w '%{http_code}' --max-time 2 \
            -H 'Host: workloads.canfar.net' \
            "http://127.0.0.1:${HOST_PORT}/session/contrib/${SESSION_ID}/" \
            2>/dev/null || echo 000
    )"
    # Early :5000 bind serves STARTING_HTML as 200 before dsh has a token.
    # Ready means token redirect (302) or boot.log captured the web token.
    if [[ "${code}" == "302" ]]; then
        ready=1
        break
    fi
    if grep -q 'dsh web token captured for Skaha Connect redirect' \
            "${FAKE_HOME}/.astroai/lab/boot.log" 2>/dev/null; then
        ready=1
        break
    fi
    sleep 2
done

if [[ "${ready}" != "1" ]]; then
    echo "FAIL: Connect path never answered 302/200 (last=${code})" >&2
    docker logs "${NAME}" 2>&1 | tail -50 >&2 || true
    exit 1
fi

BOOT="${FAKE_HOME}/.astroai/lab/boot.log"
RUNTIME="${FAKE_HOME}/.astroai/lab/agent-runtime.log"
if ! grep -q 'dsh web token captured for Skaha Connect redirect' "${BOOT}" 2>/dev/null; then
    echo "FAIL: boot.log missing dsh web token capture" >&2
    tail -40 "${BOOT}" >&2 || true
    exit 1
fi
if grep -q 'WARN: dsh web token not found' "${BOOT}" 2>/dev/null; then
    echo "FAIL: boot.log reports missing dsh web token" >&2
    exit 1
fi
if ! grep -q 'restore:\.dsh/sessions\|restore:\.dsh/storages' "${RUNTIME}" 2>/dev/null; then
    echo "FAIL: agent layout did not restore durable ~/.dsh dirs" >&2
    cat "${RUNTIME}" >&2 || true
    exit 1
fi
if [[ -L "${FAKE_HOME}/.dsh/sessions" || -L "${FAKE_HOME}/.dsh/storages" ]]; then
    echo "FAIL: ~/.dsh sessions/storages still symlinks after layout" >&2
    ls -la "${FAKE_HOME}/.dsh" >&2
    exit 1
fi
if [[ ! -d "${FAKE_HOME}/.dsh/sessions" || ! -d "${FAKE_HOME}/.dsh/storages" ]]; then
    echo "FAIL: ~/.dsh sessions/storages not real directories" >&2
    ls -la "${FAKE_HOME}/.dsh" >&2
    exit 1
fi

TOK="$(
    curl -sS -D- -o /dev/null --max-time 5 \
        -H 'Host: workloads.canfar.net' \
        "http://127.0.0.1:${HOST_PORT}/session/contrib/${SESSION_ID}/" \
        | grep -oE 'token=[A-Za-z0-9_-]+' | head -1 | cut -d= -f2-
)"
if [[ -z "${TOK}" ]]; then
    echo "FAIL: token redirect Location missing" >&2
    exit 1
fi

rm -f /tmp/studio-local-cj.$$
curl -sS -c "/tmp/studio-local-cj.$$" -o /dev/null --max-time 10 \
    -H 'Host: workloads.canfar.net' \
    "http://127.0.0.1:${HOST_PORT}/session/contrib/${SESSION_ID}/?token=${TOK}"
page="$(
    curl -sS -b "/tmp/studio-local-cj.$$" -o /tmp/studio-local-page.$$ -w '%{http_code}' \
        --max-time 10 -H 'Host: workloads.canfar.net' \
        "http://127.0.0.1:${HOST_PORT}/session/contrib/${SESSION_ID}/"
)"
rm -f "/tmp/studio-local-cj.$$"
if [[ "${page}" != "200" ]]; then
    echo "FAIL: authenticated index returned ${page}" >&2
    exit 1
fi
if ! grep -q 'data-astroai-proxy-rev=' /tmp/studio-local-page.$$; then
    echo "FAIL: HTML missing studio proxy fingerprint" >&2
    exit 1
fi
rm -f /tmp/studio-local-page.$$

echo "OK: studio stamp+dangling .dsh boot → token → 200 (${IMAGE})"
