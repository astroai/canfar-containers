#!/bin/bash -e
# Post-push smoke test on CANFAR using a headless Skaha session.
#
# Requires: canfar CLI authenticated (canfar auth login)
#
# Usage:
#   ./scripts/test-canfar.sh [image] [tag]
#   ./scripts/test-canfar.sh base 26.06
#   ./scripts/test-canfar.sh terminal latest
#
# Environment:
#   REGISTRY   default images.canfar.net
#   OWNER      default astroai
#   CANFAR_TEST_TIMEOUT  seconds to wait for session (default 1800; agent installs need time)
#   CANFAR_TEST_QUICK    1 → canfar-verify.sh --quick (PATH + CADC CLIs only)
#   CANFAR_TEST_AGENTS   1 → canfar-verify.sh --agents (lightweight agent
#                           verb-surface probe, no tool installs)
#
# Harbor images are public — no registry credentials for normal pulls.
# Optional: CANFAR_REGISTRY__* / REGISTRY_USER for maintainer headless smoke tests.

IMAGE="${1:-base}"
TAG="${2:-${TAG:-latest}}"
OWNER="${OWNER:-astroai}"
REGISTRY="${REGISTRY:-images.canfar.net}"
TIMEOUT="${CANFAR_TEST_TIMEOUT:-1800}"
FULL_IMAGE="${REGISTRY}/${OWNER}/${IMAGE}:${TAG}"
# Skaha session names: alphanumeric and hyphen only (TAG like 26.06 is invalid).
TAG_SAFE="$(printf '%s' "${TAG}" | tr '.:/+' '-' | tr -cd 'a-zA-Z0-9-')"
SESSION_NAME="astroai-verify-${IMAGE}-${TAG_SAFE}-$(date -u +%Y%m%d%H%M%S)"
FAILURES=0

maybe_registry_auth() {
    if [[ -n "${CANFAR_REGISTRY__USERNAME:-}" && -n "${CANFAR_REGISTRY__SECRET:-}" ]]; then
        export CANFAR_REGISTRY__URL="${CANFAR_REGISTRY__URL:-https://${REGISTRY}}"
        echo "Using Harbor credentials from environment (user: ${CANFAR_REGISTRY__USERNAME})"
        return 0
    fi

    if [[ -n "${REGISTRY_USER:-}" && -n "${REGISTRY_PASSWORD:-}" ]]; then
        export CANFAR_REGISTRY__USERNAME="${REGISTRY_USER}"
        export CANFAR_REGISTRY__SECRET="${REGISTRY_PASSWORD}"
        export CANFAR_REGISTRY__URL="${CANFAR_REGISTRY__URL:-https://${REGISTRY}}"
        echo "Using Harbor credentials from REGISTRY_USER (user: ${CANFAR_REGISTRY__USERNAME})"
        return 0
    fi

    unset CANFAR_REGISTRY__USERNAME CANFAR_REGISTRY__SECRET CANFAR_REGISTRY__URL
}

load_docker_registry_auth() {
    local docker_cfg="${HOME}/.docker/config.json"
    [[ -f "${docker_cfg}" ]] || return 1

    local _creds=()
    mapfile -t _creds < <(
        python3 - "${REGISTRY}" "${docker_cfg}" <<'PY'
import base64
import json
import sys

registry, path = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as fh:
    cfg = json.load(fh)
entry = cfg.get("auths", {}).get(registry, {})
if "auth" in entry:
    user, secret = base64.b64decode(entry["auth"]).decode().split(":", 1)
elif entry.get("username") and entry.get("password"):
    user, secret = entry["username"], entry["password"]
else:
    sys.exit(1)
if not user or not secret:
    sys.exit(1)
print(user)
print(secret)
PY
    ) || return 1
    [[ ${#_creds[@]} -ge 2 ]] || return 1

    export CANFAR_REGISTRY__USERNAME="${_creds[0]}"
    export CANFAR_REGISTRY__SECRET="${_creds[1]}"
    export CANFAR_REGISTRY__URL="${CANFAR_REGISTRY__URL:-https://${REGISTRY}}"
    echo "Retrying with Harbor credentials from docker login (user: ${CANFAR_REGISTRY__USERNAME})"
}

is_registry_auth_error() {
    printf '%s\n' "$1" | grep -qiE 'No authentication provided|private image|registry.auth|Registry-Auth'
}

create_headless_session() {
    # Small requests schedule more reliably on busy clusters (defaults often Pending).
    local cpu="${CANFAR_TEST_CPU:-1}"
    local memory="${CANFAR_TEST_MEMORY:-1}"
    local cmd=(bash /opt/astroai/bin/canfar-verify.sh)
    if [[ "${CANFAR_TEST_AGENTS:-0}" == "1" ]]; then
        cmd=(bash /opt/astroai/bin/canfar-verify.sh --agents)
    elif [[ "${CANFAR_TEST_QUICK:-0}" == "1" ]]; then
        cmd=(bash /opt/astroai/bin/canfar-verify.sh --quick)
    fi
    canfar create --name "${SESSION_NAME}" --cpu "${cpu}" --memory "${memory}" \
        headless "${FULL_IMAGE}" -- "${cmd[@]}" 2>&1
}

registry_auth_hint() {
    cat >&2 <<EOF
Harbor could not pull ${FULL_IMAGE} through Skaha without registry authentication.

Confirm the astroai Harbor project is **public** (anonymous docker pull works):
  docker logout ${REGISTRY}; docker pull ${FULL_IMAGE}

Science Portal session launches use registered images and do not require users to
configure registry credentials. Headless canfar create for maintainer smoke tests may
still need Harbor CLI credentials until Skaha catalogs astroai/* images:

  canfar config set registry.username <harbor-user>
  canfar config set registry.secret <harbor-cli-secret>
  canfar config set registry.url https://${REGISTRY}

Maintainers with docker login ${REGISTRY} can retry — test-canfar.sh will auto-load
those credentials on the second attempt.
EOF
}

if ! command -v canfar >/dev/null 2>&1; then
    echo "canfar CLI not found. Install with: uv tool install canfar" >&2
    exit 1
fi

if ! canfar auth show >/dev/null 2>&1; then
    echo "canfar is not authenticated. Run: canfar auth login" >&2
    exit 1
fi

session_status() {
    canfar_ps_field id "$1" status
}

canfar_ps_field() {
    local match_key="$1" match_val="$2" want_field="$3"
    canfar ps -a --json 2>/dev/null | python3 -c "
import json, sys

match_key, match_val, want_field = sys.argv[1:4]
raw = sys.stdin.read().strip()
if not raw:
    raise SystemExit(0)
for marker in ('[', '{'):
    idx = raw.find(marker)
    if idx >= 0:
        raw = raw[idx:]
        break
try:
    rows = json.loads(raw)
except json.JSONDecodeError:
    raise SystemExit(0)
if isinstance(rows, dict):
    rows = [rows]
for row in rows:
    if row.get(match_key) == match_val:
        val = row.get(want_field, '')
        if val:
            print(val)
        break
" "${match_key}" "${match_val}" "${want_field}" || true
}

cleanup() {
    if [[ -n "${SESSION_ID:-}" ]]; then
        echo ""
        echo "Deleting test session ${SESSION_ID}..."
        canfar delete --force "${SESSION_ID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "CANFAR headless verification"
echo "  image:   ${FULL_IMAGE}"
echo "  name:    ${SESSION_NAME}"
echo "  timeout: ${TIMEOUT}s"
echo ""

# Harbor project is public — try without registry auth first (Science Portal path).
# ponytail: retry with docker login creds → remove when Skaha catalogs public astroai/* pulls
maybe_registry_auth

CREATE_RC=0
CREATE_OUT="$(create_headless_session)" || CREATE_RC=$?
if [[ "${CREATE_RC}" -ne 0 ]]; then
    if is_registry_auth_error "${CREATE_OUT:-}" && load_docker_registry_auth; then
        CREATE_RC=0
        CREATE_OUT="$(create_headless_session)" || CREATE_RC=$?
    fi
fi
if [[ "${CREATE_RC}" -ne 0 ]]; then
    echo "${CREATE_OUT}" >&2
    if is_registry_auth_error "${CREATE_OUT:-}"; then
        echo "" >&2
        registry_auth_hint
    fi
    echo "Failed to create headless session." >&2
    FAILURES=$((FAILURES + 1))
    exit ${FAILURES}
fi

echo "${CREATE_OUT}"

SESSION_ID="$(
    printf '%s\n' "${CREATE_OUT}" \
        | tr -d '\r' \
        | tr '\n' ' ' \
        | sed -n 's/.*(ID:[[:space:]]*\([^)]*\)).*/\1/p' \
        | awk '{print $1}'
)"
if [[ -z "${SESSION_ID}" ]]; then
    SESSION_ID="$(canfar_ps_field name "${SESSION_NAME}" id)"
fi
if [[ -z "${SESSION_ID}" ]]; then
    echo "Could not parse session ID from canfar create output." >&2
    exit 1
fi

echo "Session ID: ${SESSION_ID}"
echo "Waiting for completion (poll every 10s)..."

# Fail-fast when headless never acquires Start Time (platform admission hang).
# See docs/OPERATORS.md (platform notes).
PENDING_STUCK_SECS="${CANFAR_PENDING_STUCK_SECS:-120}"
pending_since=""
deadline=$((SECONDS + TIMEOUT))
status=""
while (( SECONDS < deadline )); do
    status="$(session_status "${SESSION_ID}")"
    case "${status}" in
        Succeeded|Completed)
            echo "Session finished (${status})."
            break
            ;;
        Failed|Error|Terminating)
            echo "Session ended with status: ${status}" >&2
            break
            ;;
        Pending)
            if [[ -z "${pending_since}" ]]; then
                pending_since="${SECONDS}"
            fi
            start_info="$(canfar info "${SESSION_ID}" 2>/dev/null || true)"
            if printf '%s\n' "${start_info}" | grep -q 'Start Time[[:space:]]\+Unknown' \
                && (( SECONDS - pending_since >= PENDING_STUCK_SECS )); then
                echo "" >&2
                echo "Headless session still Pending with Start Time Unknown after ${PENDING_STUCK_SECS}s." >&2
                echo "This usually indicates a Skaha headless-scheduling hang (not image CMD)." >&2
                echo "See docs/OPERATORS.md (platform notes) — use: make test-canfar-session IMAGE=<terminal|notebook|…>" >&2
                echo "Headless kinds are quota-exempt; this Pending hang is the Skaha scheduling flake (opencadc/science-platform#1124), not a concurrent-session quota lock." >&2
                canfar logs "${SESSION_ID}" 2>&1 | tail -20 >&2 || true
                FAILURES=$((FAILURES + 1))
                exit ${FAILURES}
            fi
            ;;
        "")
            # Session may not appear in ps immediately
            ;;
        *)
            pending_since=""
            ;;
    esac
    sleep 10
done

if [[ "${status}" != "Succeeded" && "${status}" != "Completed" ]]; then
    echo ""
    echo "=== Session logs ==="
    canfar logs "${SESSION_ID}" 2>&1 || true
    echo ""
    echo "Verification failed (status: ${status:-timeout})." >&2
    FAILURES=$((FAILURES + 1))
    exit ${FAILURES}
fi

echo ""
echo "=== Session logs ==="
LOGS="$(canfar logs "${SESSION_ID}" 2>&1 || true)"
printf '%s\n' "${LOGS}"

if printf '%s\n' "${LOGS}" | grep -q "All checks passed."; then
    echo ""
    echo "CANFAR headless verification passed for ${FULL_IMAGE}."
    exit ${FAILURES}
fi

echo ""
echo "Session succeeded but verification output missing success marker." >&2
FAILURES=$((FAILURES + 1))
exit ${FAILURES}
