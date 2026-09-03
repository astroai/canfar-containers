#!/usr/bin/env bash
# Harbor cleanup for astroai/* image tags on images.canfar.net.
#
# For each repository in KNOWN_REPOS under OWNER (default: astroai), delete
# every tag whose digest does NOT match the digest of any tag in KEEP_TAGS
# (default: this month, last month, and latest).
#
# Algorithm:
#   1. For each kept tag, HEAD /v2/<repo>/manifests/<tag> with the docker
#      Basic-Auth token and capture Docker-Content-Digest. (Harbor returns
#      401 to anonymous tiles/list; it does accept authenticated heads.)
#   2. For each candidate non-kept tag, HEAD the same way and capture digest.
#      - If the digest matches a kept tag's digest, SKIP (deleting it would
#        orphan the kept tag from its underlying blobs; Harbor's GC would then
#        free the blobs the kept tag still references).
#      - If the digest differs and resolves, DELETE /v2/<repo>/manifests/<digest>.
#      - If HEAD returns nothing (404/405/missing tag), just skip.
#
# Why hardcoded repos and candidate tags: Harbor's /v2/_catalog and
# /v2/<repo>/tags/list endpoints return 401 to the docker-config token we
# have (lacks the `catalog:*` and `repository:...:list` scopes). We probe a
# fixed list of likely prior-release tags instead, since images are pushed only
# via `make push-*` and tags follow `YY.MM` (or `local`/`sha-...`/`latest`).
#
# DESTRUCTIVE: every successful DELETE removes a manifest. Harbor's garbage
# collector reclaims orphans on its next run. There is no undo.
#
# Usage:
#   DRY_RUN=1 ./scripts/harbor-cleanup.sh        # preview only
#   ./scripts/harbor-cleanup.sh                  # destructive
#
# Environment overrides:
#   REGISTRY         default: images.canfar.net
#   OWNER            default: astroai
#   KEEP_TAGS        default: "26.09 26.08 latest"   (space-separated)
#   KNOWN_REPOS      default: session + ray + improc images (see script)
#   CANDIDATE_TAGS   default: current + prior YY.MM tags, latest, local, sha-
#   DOCKER_CONFIG    default: $HOME/.docker/config.json

set -euo pipefail

REGISTRY="${REGISTRY:-images.canfar.net}"
OWNER="${OWNER:-astroai}"
KEEP_TAGS="${KEEP_TAGS:-26.09 26.08 latest}"
KNOWN_REPOS="${KNOWN_REPOS:-base webterm ghostty-web vscode notebook marimo openresearch improc improc-webterm improc-notebook ray-manager ray-worker}"
CANDIDATE_TAGS_DEFAULT='26.09 26.08 latest 26.07 26.06 26.05 26.04 25.12 25.11 25.10 25.09 25.08 25.07 25.06 25.05 25.04 25.03 25.02 25.01 24.12 24.11 24.10 24.09 24.08 24.07 local sha-'
CANDIDATE_TAGS="${CANDIDATE_TAGS:-${CANDIDATE_TAGS_DEFAULT}}"
DRY_RUN="${DRY_RUN:-0}"
DOCKER_CONFIG="${DOCKER_CONFIG:-$HOME/.docker/config.json}"

api_base() { printf 'https://%s/v2' "${REGISTRY}"; }

# auth_token <docker_config_path> <registry> -> base64(user:pass) on stdout
auth_token() {
    python3 - "$1" "$2" <<'PY'
import base64, json, sys
cfg_path, registry = sys.argv[1], sys.argv[2]
cfg = json.load(open(cfg_path))
auth = cfg.get("auths", {}).get(registry, {})
if "auth" in auth:
    token = auth["auth"].strip()
    if not token:
        sys.exit("empty auth field for " + registry)
    print(token)
elif auth.get("username"):
    print(base64.b64encode(f"{auth['username']}:{auth.get('password','')}".encode()).decode())
else:
    sys.exit("no credentials found for " + registry + " in " + cfg_path)
PY
}

# api_head_digest <path> -- single-line docker-content-digest on stdout,
# empty on 404 / 405 / other 4xx (we drop -f so curl never exits non-zero).
api_head_digest() {
    curl -sSI --max-time 30 \
        -H "Authorization: Basic ${TOKEN}" \
        -H 'Accept: application/vnd.docker.distribution.manifest.v2+json' \
        -H 'Accept: application/vnd.oci.image.manifest.v1+json' \
        "$1" \
        | tr -d '\r' \
        | awk 'tolower($1) == "docker-content-digest:" {print $2; exit}'
}

# api_delete <path> -- DELETE that fails loudly on non-2xx (so we count errors)
api_delete() {
    if [[ "${DRY_RUN}" == "1" ]]; then
        printf '    [DRY-RUN] DELETE %s\n' "$1" >&2
        return 0
    fi
    curl -fsS --max-time 60 -X DELETE \
        -H "Authorization: Basic ${TOKEN}" "$1"
}

require_cmd() {
    for c in "$@"; do
        command -v "${c}" >/dev/null 2>&1 || {
            echo "required command not found: ${c}" >&2; exit 1
        }
    done
}

require_cmd curl python3 awk

if [[ ! -r "${DOCKER_CONFIG}" ]]; then
    echo "Docker config not readable: ${DOCKER_CONFIG}" >&2
    echo "Run: docker login ${REGISTRY}" >&2
    exit 1
fi

TOKEN="$(auth_token "${DOCKER_CONFIG}" "${REGISTRY}")"
[[ -n "${TOKEN}" ]] || { echo "empty auth token from ${DOCKER_CONFIG}" >&2; exit 1; }

read -r -a REPOS <<< "${KNOWN_REPOS}"
read -r -a KEEP_ARR <<< "${KEEP_TAGS}"
read -r -a CAND_ARR <<< "${CANDIDATE_TAGS}"
API="$(api_base)"

printf 'Harbor cleanup\n  registry : %s\n  owner    : %s\n  keep     : %s\n  dry-run  : %s\n  repos    : %s\n\n' \
    "${REGISTRY}" "${OWNER}" "${KEEP_TAGS// /, }" "${DRY_RUN}" "${KNOWN_REPOS}"

deleted=0
skipped=0
missed=0
errors=0

for repo in "${REPOS[@]}"; do
    printf '==> %s/%s\n' "${OWNER}" "${repo}"

    declare -A keep_digest=()
    declare -A seen=()
    for k in "${KEEP_ARR[@]}"; do
        d="$(api_head_digest "${API}/${OWNER}/${repo}/manifests/${k}")"
        if [[ -n "${d}" ]]; then
            keep_digest["${d}"]=1
            seen["${k}"]=1
            printf '  keep  :%s -> %s\n' "${k}" "${d}"
        else
            printf '  warn  :%s in KEEP_TAGS but HEAD returned no digest\n' "${k}" >&2
        fi
    done

    for tag in "${CAND_ARR[@]}"; do
        [[ -n "${seen[${tag}]:-}" ]] && continue

        digest="$(api_head_digest "${API}/${OWNER}/${repo}/manifests/${tag}")"
        if [[ -z "${digest}" ]]; then
            # 404/405 — tag does not exist on this repo
            continue
        fi

        if [[ -n "${keep_digest[${digest}]:-}" ]]; then
            printf '  skip  :%s (digest %s shared with a kept tag)\n' "${tag}" "${digest}"
            skipped=$((skipped + 1))
            continue
        fi

        printf '  del   :%s (digest %s)\n' "${tag}" "${digest}"
        if api_delete "${API}/${OWNER}/${repo}/manifests/${digest}"; then
            deleted=$((deleted + 1))
        else
            printf '  fail  :%s DELETE failed\n' "${tag}" >&2
            errors=$((errors + 1))
        fi
    done
    unset keep_digest seen
    echo
done

printf '\n=== Summary ===\ndeleted manifests    : %d\nskipped (shared)     : %d\nerrors               : %d\n' \
    "${deleted}" "${skipped}" "${errors}"

(( errors == 0 )) || exit 2
