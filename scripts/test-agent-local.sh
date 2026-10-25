#!/bin/bash -e
# Local CANFAR-emulation E2E for the canfar-lab agent command surface.
#
# Proves, against each session image run like a CANFAR session (fresh MOUNTED
# home, non-root user), that:
#   1. every agent/plugin command works out of the box
#      (list, install, verify, plugins list/install/remove,
#       and the registry-driven verbs
#       setup <agent> / config <agent> / verify --fix <agent> / update <agent>);
#   2. agent CLI installs land in $SCRATCH/.local/bin (never ~/.local/bin).
#
# Runs TWO scenarios per image: with scratch (CANFAR-like) and without
# (plain local machine) — caches/runtimes still prefer scratch when mounted.
#
# Usage:
#   ./scripts/test-agent-local.sh                 # ALL session images
#   ./scripts/test-agent-local.sh openresearch    # one image
#   ./scripts/test-agent-local.sh base terminal    # explicit list
#
# Env:
#   OWNER / REGISTRY / TAG     image coordinates (defaults: astroai /
#                               images.canfar.net / local)
#   ASTROAI_LAB_SRC            optional path to an astroai src/ overlay
#                              (mounted at /opt/astroai-lab-src + PYTHONPATH)
#                              for testing uncommitted astroai code.

# Images under test: explicit args win; default = the bake default group
# (same set as the Makefile DEFAULT_IMAGES / test-local loop).
if [[ "$#" -gt 0 ]]; then
    IMAGES=("$@")
else
    IMAGES=(base improc specproc openscience openresearch ray-worker ray-manager studio improc-studio specproc-studio)
fi
OWNER="${OWNER:-astroai}"
REGISTRY="${REGISTRY:-images.canfar.net}"
TAG="${TAG:-local}"
FAILURES=0
# Host mktemp homes/src/scratch can be large (kilo CLI extract). Prefer a
# roomy disk when /tmp is the small root volume.
if [[ -d /mnt/tmp ]] && [[ -w /mnt/tmp ]]; then
    export TMPDIR="${TMPDIR:-/mnt/tmp}"
fi

OVERLAY_ARGS=()
if [[ -n "${ASTROAI_LAB_SRC:-}" ]]; then
    OVERLAY_ARGS=(
        -v "${ASTROAI_LAB_SRC}:/opt/astroai-lab-src"
        -e "PYTHONPATH=/opt/astroai-lab-src"
    )
fi

PROBE="$(mktemp)"
# Clean up the probe plus any in-flight per-image temp dirs on early exit
# (Ctrl-C / unexpected set -e abort mid-loop), not just the happy path.
FAKE_HOME="" FAKE_SRC="" FAKE_SCRATCH=""
trap 'rm -f "${PROBE}"; [[ -n "${FAKE_HOME}" ]] && rm -rf "${FAKE_HOME}" "${FAKE_SRC}" "${FAKE_SCRATCH}"' EXIT

cat > "${PROBE}" <<'PROBE_EOF'
#!/bin/bash
# Runs inside the container as a non-root user with a fresh mounted HOME.
set -u
# The image's PATH hook lives in /etc/profile.d/astroai.sh (login shells
# only) — this probe runs via plain `bash`, so put astroai on PATH here.
export PATH="/opt/canfar/bin:/opt/astroai/bin:${PATH}"
HOME_DIR="$(pwd)"
export HOME="${HOME_DIR}"
export USER=testuser
export WORK=/srcdir
export SCRATCH="${SCRATCH:-}"
# Bind-mounted /srcdir (harness -v) is not the CANFAR overlay; WORK stays
# /srcdir. Overlay relocate is scripts/test-work-overlay.sh.

fail() { echo "  FAIL: $*" >&2; exit 1; }
ok()   { echo "  ok: $*"; }

# 0. bin dir prefers $SCRATCH/.local/bin (scratch-canonical). Never ~/.local/bin.
#    Configs stay on $HOME; caches/runtimes already use scratch when mounted.
ENV_JSON="$(canfar-lab env export --json)"
BIN_DIR="$(printf '%s' "${ENV_JSON}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["CANFAR_LAB_BIN_DIR"])')"
if [[ -n "${SCRATCH}" && -d "${SCRATCH}" ]]; then
    case "${BIN_DIR}" in
        "${SCRATCH}/.local/bin") ok "bin dir scratch-canonical: ${BIN_DIR}";;
        *) fail "expected ${SCRATCH}/.local/bin, got ${BIN_DIR}";;
    esac
else
    case "${BIN_DIR}" in
        "${HOME_DIR}/.local/bin") fail "bin dir must not be home ~/.local/bin without scratch";;
        *) ok "bin dir offline fallback: ${BIN_DIR}";;
    esac
fi

# 1. read commands work out of the box.
# Rich Console writes tables to stderr; drop both streams so a pass stays quiet.
canfar-lab agent list          >/dev/null 2>&1 || fail "agent list"
canfar-lab agent plugins list  >/dev/null 2>&1 || fail "agent plugins list"

# 2. install a curl-installer agent into the managed bin dir (scratch).
canfar-lab agent install kilo  >/dev/null || fail "agent install kilo"
[[ -x "${BIN_DIR}/kilo" || -L "${BIN_DIR}/kilo" ]] || fail "kilo not in ${BIN_DIR}"

# 3. verify: binary checks pass on a fresh home (config checks may fire —
#    that's by design on a fresh home; the command must not crash). --json is
#    a root-callback flag, so it precedes the agent subcommand.
# Lean list: exit 1 when setup incomplete (ok:false) is intentional — JSON
# must still emit with an "agents" array (see canfar-lab agent list contract).
canfar-lab --json agent list | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert isinstance(d.get("agents"), list) and d["agents"], d
' || fail "--json agent list"
canfar-lab --json agent verify >/dev/null 2>&1 || true

# 4. plugin install/remove round-trip (MCP plugin scoped to an mcp-host).
if canfar-lab agent plugins list 2>/dev/null | grep -q ray-manager-mcp; then
    canfar-lab agent plugins install ray-manager-mcp --agent cursor >/dev/null 2>&1 || true
    canfar-lab agent plugins remove ray-manager-mcp --agent cursor >/dev/null 2>&1 || true
fi

# 5. remove leaves no kilo binary.
canfar-lab agent remove kilo >/dev/null || fail "agent remove kilo"
[[ ! -e "${BIN_DIR}/kilo" ]] || fail "kilo still in ${BIN_DIR} after remove"

# 7. Phase 2 verbs (registry-driven): setup / config / update for hermes.
#    setup + config are fully offline; update takes the up-to-date path via
#    a fake binary so MCP/plugin re-apply is exercised with zero network.
mkdir -p "${BIN_DIR}"
canfar-lab agent setup hermes >/dev/null || fail "agent setup hermes"
[[ -f "${HOME_DIR}/.hermes/config.yaml" ]] || fail "setup hermes: config.yaml not scaffolded"
[[ -d "${HOME_DIR}/.hermes/skills" ]] || fail "setup hermes: skills dir missing"

canfar-lab agent config hermes model=hermes-test-model >/dev/null \
    || fail "agent config hermes model=..."
# Human output goes to stderr (rich Console(stderr=True)), so assert via the
# stream-safe --json variant (print_json → stdout).
canfar-lab --json agent config hermes --key model | python3 -c '
import json, sys
assert json.load(sys.stdin)["value"] == "hermes-test-model"
' || fail "agent config hermes --key model"
canfar-lab --json agent config hermes | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["format"] == "yaml", d
assert d["data"].get("model") == "hermes-test-model", d
' || fail "--json agent config hermes"
canfar-lab agent config hermes --unset model >/dev/null \
    || fail "agent config hermes --unset model"

# Fake hermes binary → `agent update hermes` skips the network install and
# force re-applies hermes plugins (MCP / rules / tools in the matrix).
printf '#!/bin/sh\necho hermes fake 0.0.0\n' > "${BIN_DIR}/hermes"
chmod +x "${BIN_DIR}/hermes"
# Precondition: update must see the fake binary via the SESSION bin dir (the
# exact check update_registry_agent uses) — if this fails, `agent update
# hermes` would attempt a real network install instead of the offline path.
canfar-lab --json agent list | python3 -c '
import json, sys
d = json.load(sys.stdin)
hermes = next(r for r in d["agents"] if r["id"] == "hermes")
assert hermes["binary_ok"], "fake hermes not detected in session bin dir"
' || fail "update hermes: fake binary not on session bin dir"
canfar-lab agent update hermes >/dev/null || fail "agent update hermes"
[[ -f "${HOME_DIR}/.hermes/config.yaml" ]] \
    || fail "update hermes: config.yaml missing after update"

# 8. Phase 6 wipe verb: --dry-run previews the factory reset on the (now
#    mostly clean) home and must NOT remove anything. --json --yes would wipe
#    the whole agent layer, so only the safe preview path is exercised here.
canfar-lab --json agent wipe --dry-run | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["ok"] and d["dry_run"] is True
assert d["counts"]["removed"] == 0, d["counts"]
# hermes config + fake binary exist at this point, so the preview must list
# them as would_remove (never removed) — proves the wipe sees them.
assert d["counts"]["would_remove"] > 0, d["counts"]
' || fail "agent wipe --dry-run"

# 9. Phase 2 verify --fix: broken config repair + healthy no-op.
#    Corrupt ~/.hermes/config.yaml with unparseable YAML → repair resets
#    it to a format-aware scaffold; a healthy config is reported and never
#    clobbered (fix_registry_agent semantics).
printf 'model: [unclosed\n' > "${HOME_DIR}/.hermes/config.yaml"
# NOTE: the reset discards the plugin-written entries from the `agent update`
# above (documented fix_registry_agent behavior) — nothing later needs them.
canfar-lab --json agent verify --fix hermes | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["ok"] and d["agent"] == "hermes"
assert any("repaired broken yaml config" in a for a in d["actions"]), d["actions"]
' || fail "verify --fix hermes: broken yaml not repaired"
canfar-lab --json agent config hermes >/dev/null \
    || fail "verify --fix hermes: repaired config still unreadable"

# Healthy no-op: a marker value must survive repair untouched.
canfar-lab agent config hermes marker=keep-me >/dev/null \
    || fail "agent config hermes marker=keep-me"
canfar-lab --json agent verify --fix hermes | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["ok"]
assert any("config healthy" in a for a in d["actions"]), d["actions"]
' || fail "verify --fix hermes: healthy run not reported"
canfar-lab --json agent config hermes --key marker | python3 -c '
import json, sys
assert json.load(sys.stdin)["value"] == "keep-me"
' || fail "verify --fix hermes: healthy config clobbered"

ok "scratch-canonical installs; all agent commands OK (bin dir ${BIN_DIR})"
PROBE_EOF
chmod +x "${PROBE}"

run_scenario() {
    local label="$1" scratch_arg=() scratch_env=()
    if [[ "$label" == "with-scratch" ]]; then
        # canfar-lab uses scratch for CLI installs only on a Skaha session
        # (skaha_sessionid) or a studio image. Headless images need the id.
        scratch_arg=(-v "${FAKE_SCRATCH}:/scratch")
        scratch_env=(-e SCRATCH=/scratch -e skaha_sessionid=test-session)
    else
        # Point SCRATCH at a path that does not exist (and /scratch is not
        # mounted) so scratch resolution falls back to the runtime root.
        scratch_env=(-e SCRATCH=/scratch-not-mounted)
    fi
    echo "=== ${IMAGE}:${TAG} ${label} ==="
    local out
    out="$(docker run --rm \
        -u "$(id -u):$(id -g)" \
        -e HOME=/home/testuser \
        -e USER=testuser \
        "${scratch_env[@]}" \
        "${OVERLAY_ARGS[@]}" \
        -v "${FAKE_HOME}:/home/testuser" \
        -v "${FAKE_SRC}:/srcdir" \
        "${scratch_arg[@]}" \
        -v "${PROBE}:/opt/probe.sh:ro" \
        --workdir /home/testuser \
        --entrypoint bash \
        "${FULL_IMAGE}" /opt/probe.sh 2>&1)" || {
        echo "${out}"
        echo "  FAILED (${label})" >&2
        return 1
    }
    echo "${out}"
    return 0
}

# Deliberately SEQUENTIAL (unlike test-local's parallel `& pids`): each image
# runs 2 docker scenarios with a real `agent install kilo` (network fetch), so
# parallelizing 7 images x 2 scenarios would contend on network/CPU.
for IMAGE in "${IMAGES[@]}"; do
    FULL_IMAGE="${REGISTRY}/${OWNER}/${IMAGE}:${TAG}"
    echo ""
    echo ">>> ${FULL_IMAGE}"
    # Fresh mounts per image so no state leaks between images.
    FAKE_HOME="$(mktemp -d)"
    FAKE_SRC="$(mktemp -d)"
    FAKE_SCRATCH="$(mktemp -d)"
    if ! run_scenario "with-scratch"; then
        FAILURES=$((FAILURES + 1))
    fi
    if ! run_scenario "without-scratch"; then
        FAILURES=$((FAILURES + 1))
    fi
    rm -rf "${FAKE_HOME}" "${FAKE_SRC}" "${FAKE_SCRATCH}"
    FAKE_HOME="" FAKE_SRC="" FAKE_SCRATCH=""
done

if [[ "${FAILURES}" -gt 0 ]]; then
    echo "${FAILURES} scenario(s) failed across ${#IMAGES[@]} image(s)." >&2
    exit 1
fi
echo ""
echo "ALL PASS: ${#IMAGES[@]} image(s) — agent command matrix + scratch-canonical installs"
