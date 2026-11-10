#!/bin/bash
# Local astroai/dsh boot check, Skaha-shaped (session path, /arc home, /scratch).
#
# Session 1 (new user): Connect → dsh token → Agent page with tab strip;
#   /lab/ page and onboarding say "new"; Lab refuses requests that bypass the
#   proxy; save $SCRATCH/src to a snapshot through the proxy.
# Session 2 (returning user, same home, empty scratch): the snapshot comes
#   back by itself and onboarding says "returning".
#
# Usage: TAG=local ./scripts/test-dsh-local.sh   (make test-dsh-local BUILD_TAG=local)
set -euo pipefail

OWNER="${OWNER:-astroai}"
REGISTRY="${REGISTRY:-images.canfar.net}"
TAG="${TAG:-${BUILD_TAG:-local}}"
IMAGE="${REGISTRY}/${OWNER}/dsh:${TAG}"
HOST_PORT="${DSH_TEST_PORT:-15060}"
SESSION_ID="dsh-local-$$"
BASE="http://127.0.0.1:${HOST_PORT}/session/contrib/${SESSION_ID}"
NAME="dsh-local-test-$$"

WORKDIR="$(mktemp -d -p "${TMPDIR:-/tmp}" dshtest.XXXX)"
FAKE_HOME="${WORKDIR}/home"
mkdir -p "${FAKE_HOME}" "${WORKDIR}/scratch1" "${WORKDIR}/scratch2"
chmod -R a+rwX "${WORKDIR}"
JAR="${WORKDIR}/cookies"
cleanup() {
    docker rm -f "${NAME}" >/dev/null 2>&1 || true
    # Files written by the container user; remove from inside a container.
    docker run --rm -v "${WORKDIR}:/w" --entrypoint rm "${IMAGE}" -rf /w/home /w/scratch1 /w/scratch2 \
        >/dev/null 2>&1 || true
    rm -rf "${WORKDIR}"
}
trap cleanup EXIT

fail() {
    echo "FAIL: $*" >&2
    docker logs "${NAME}" 2>&1 | tail -30 >&2 || true
    docker exec "${NAME}" sh -c 'tail -n 20 /scratch/.dsh-*/logs/*.log' >&2 2>/dev/null || true
    exit 1
}
req() { curl -sS --max-time 20 -H 'Host: workloads.canfar.net' "$@"; }

start_session() {
    local scratch="$1"
    docker rm -f "${NAME}" >/dev/null 2>&1 || true
    docker run -d --name "${NAME}" -u "$(id -u):$(id -g)" \
        -e HOME=/arc/home/testuser -e USER=testuser -e SCRATCH=/scratch \
        -e skaha_sessionid="${SESSION_ID}" \
        -p "${HOST_PORT}:5000" \
        -v "${FAKE_HOME}:/arc/home/testuser" -v "${scratch}:/scratch" \
        "${IMAGE}" >/dev/null
    local code=000
    for _ in $(seq 1 120); do
        docker ps -q --filter "name=^/${NAME}$" | grep -q . || fail "container exited during boot"
        code="$(req -o /dev/null -w '%{http_code}' "${BASE}/" 2>/dev/null || echo 000)"
        [[ "${code}" == "302" ]] && break
        sleep 2
    done
    [[ "${code}" == "302" ]] || fail "Connect never redirected to the dsh token (last=${code})"
    local loc tok
    loc="$(req -o /dev/null -D- "${BASE}/" | tr -d '\r' | sed -n 's/^Location: //Ip')"
    tok="${loc##*token=}"
    [[ -n "${tok}" && "${loc}" == "/session/contrib/${SESSION_ID}/?token="* ]] \
        || fail "bad Connect Location: ${loc}"
    rm -f "${JAR}"
    req -c "${JAR}" -o /dev/null "${BASE}/?token=${tok}"
}

onboarding() {  # badge summary: kind, ready, pending, next
    req -b "${JAR}" "${BASE}/lab/api/onboarding"
}
steps() {  # full checklist the Lab page renders
    req "${BASE}/lab/api/state" | jget 'json.dumps(d["onboarding"]["steps"])'
}

wait_lab() {
    for _ in $(seq 1 60); do
        [[ "$(req -o /dev/null -w '%{http_code}' "${BASE}/lab/api/health" || true)" == "200" ]] && return 0
        sleep 2
    done
    fail "Lab never answered through the proxy"
}

wait_job() {  # $1 = job id; prints final job JSON
    local doc state
    for _ in $(seq 1 120); do
        doc="$(req "${BASE}/lab/api/jobs/$1")"
        state="$(jget 'd["job"]["state"]' <<<"${doc}")"
        [[ "${state}" != "running" ]] && { echo "${doc}"; return 0; }
        sleep 1
    done
    fail "job $1 never finished"
}

jget() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

echo "dsh local boot check against ${IMAGE}"

# ---- session 1: new user ---------------------------------------------------
start_session "${WORKDIR}/scratch1"
wait_lab
# A brand-new session opens on the Lab checklist once, then the Agent tab as usual.
first="$(req -b "${JAR}" -c "${JAR}" -H 'Sec-Fetch-Dest: document' -o /dev/null -w '%{http_code} %{redirect_url}' "${BASE}/")"
[[ "${first}" == "302 "*"/session/contrib/${SESSION_ID}/lab/" ]] || fail "new session did not open on the Lab: ${first}"
page="$(req -b "${JAR}" -H 'Sec-Fetch-Dest: document' "${BASE}/")"
grep -q 'id="astroai-tabs-config"' <<<"${page}" || fail "Agent page has no tab strip"
grep -q 'data-astroai-api-shim' <<<"${page}" || fail "Agent page has no session-path shim"
[[ "$(req -o /dev/null -w '%{http_code}' "${BASE}/__dsh/tabs.js")" == "200" ]] || fail "tabs.js missing"

[[ "$(req -o /dev/null -w '%{redirect_url}' "${BASE}/lab")" == *"/session/contrib/${SESSION_ID}/lab/" ]] \
    || fail "/lab does not redirect to /lab/"
lab="$(req -H 'Sec-Fetch-Dest: document' "${BASE}/lab/")"
grep -q "<base href=\"/session/contrib/${SESSION_ID}/lab/\">" <<<"${lab}" || fail "Lab page has no session <base>"
grep -q '"active": "lab"' <<<"${lab}" || fail "Lab page tab strip is not on Lab"

ob="$(onboarding)"
[[ "$(jget 'd["kind"]' <<<"${ob}")" == "new" ]] || fail "new user not recognised: ${ob}"
[[ "$(jget 'd["ready"]' <<<"${ob}")" == "False" ]] || fail "new user reported ready: ${ob}"
[[ "$(jget 'd["next"]' <<<"${ob}")" == "agents" ]] || fail "first step is not agents: ${ob}"
st="$(steps)"
jget '[s for s in d if s["id"]=="agents"][0]["action"]["agent"]' <<<"${st}" | grep -qx opencode \
    || fail "agents step does not suggest OpenCode: ${st}"

# The Lab itself only trusts the proxy's bearer.
direct="$(docker exec "${NAME}" curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:4792/api/state)"
[[ "${direct}" == "401" ]] || fail "Lab answered ${direct} without the bearer"
nowrite="$(req -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' \
    "${BASE}/lab/api/snapshots")"
[[ "${nowrite}" == "403" ]] || fail "Lab write without X-Canfar-Lab returned ${nowrite}"

# Some code, then Save through the proxy.
docker exec "${NAME}" bash -c 'mkdir -p /scratch/src/demo && cd /scratch/src/demo && echo "print(42)" > main.py'
st="$(steps)"
jget '[s for s in d if s["id"]=="code"][0]["action"]["op"]' <<<"${st}" | grep -qx save_snapshot \
    || fail "unsaved code does not nudge Save now: ${st}"
resp="$(req -X POST -H 'X-Canfar-Lab: 1' -H 'Content-Type: application/json' -d '{"name": "smoke"}' \
    "${BASE}/lab/api/snapshots")"
job_id="$(jget 'd["job"]["id"]' <<<"${resp}")" || fail "save did not start a job: ${resp}"
events="$(req -N --max-time 60 -H 'Accept: text/event-stream' "${BASE}/lab/api/jobs/${job_id}/events" || true)"
grep -q '^data:' <<<"${events}" || fail "job log did not stream through the proxy"
final="$(wait_job "${job_id}")"
[[ "$(jget 'd["job"]["state"]' <<<"${final}")" == "ok" ]] || fail "save failed: ${final}"

# Skaha stops a session with SIGTERM: it must exit, not restart its services.
t0=$SECONDS
docker stop -t 30 "${NAME}" >/dev/null
(( SECONDS - t0 < 15 )) || fail "SIGTERM did not end the session (took $((SECONDS - t0))s)"
docker rm -f "${NAME}" >/dev/null

# ---- session 2: returning user, empty scratch ------------------------------
start_session "${WORKDIR}/scratch2"
wait_lab
for _ in $(seq 1 60); do
    docker exec "${NAME}" test -f /scratch/src/demo/main.py && break
    sleep 2
done
docker exec "${NAME}" grep -q 'print(42)' /scratch/src/demo/main.py \
    || fail "returning session did not restore the snapshot into \$SCRATCH/src"
ob="$(onboarding)"
[[ "$(jget 'd["kind"]' <<<"${ob}")" == "returning" ]] || fail "returning user not recognised: ${ob}"
code="$(req -b "${JAR}" -H 'Sec-Fetch-Dest: document' -o /dev/null -w '%{http_code}' "${BASE}/")"
[[ "${code}" == "200" ]] || fail "returning user was not sent straight to the Agent tab (${code})"

echo "OK: dsh Connect, Agent + Lab tabs, Lab auth, save, auto-restore in a new session (${IMAGE})"
