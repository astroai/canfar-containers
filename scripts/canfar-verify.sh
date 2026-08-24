#!/bin/bash -e
set -o pipefail
# Post-deploy smoke checks for AstroAI images (run inside a CANFAR session).
#
# Usage:
#   canfar-verify.sh              full check (login + non-login shells)
#   canfar-verify.sh --quick        PATH + CADC CLIs only
#   canfar-verify.sh --agents       lightweight agent verb-surface probe only
#                                   (canfar-verify-agents.sh --setup: setup,
#                                   verify, repair, plugins — no
#                                   tool installs; fast, network-light)

QUICK=0
AGENTS=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --quick) QUICK=1; shift ;;
        --agents) AGENTS=1; shift ;;
        -h|--help)
            sed -n '2,8p' "$0"
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done
# --quick and --agents are mutually exclusive: --quick skips everything
# non-quick, --agents runs ONLY the agent verb-surface probe. Combining
# them would silently produce an empty (false-green) run.
if [[ "${QUICK}" -eq 1 && "${AGENTS}" -eq 1 ]]; then
    echo "--quick and --agents are mutually exclusive" >&2
    exit 1
fi

failures=0

# ---------------------------------------------------------------------------
# Batch runner: pipe a shell script via stdin into ONE "bash -lc" call.
# The inner script must emit lines of the form "PASS:<label>" or "FAIL:<label>".
# ---------------------------------------------------------------------------
batch_login() {
    bash -lc "$(cat)"
}

# Process PASS/FAIL lines from a batch_login invocation, updating $failures.
# Each batch MUST end with "BATCH_END" — if missing, the batch crashed and
# we report a failure rather than silently dropping all its checks.
process_batch() {
    local status label _saw_end=0
    while IFS=: read -r status label; do
        case "${status}" in
            BATCH_END) _saw_end=1 ;;
            PASS)      printf '  ok  %s\n' "${label}" ;;
            FAIL)      printf '  FAIL %s\n' "${label}" >&2; failures=$((failures + 1)) ;;
            *)         printf '  FAIL (unexpected) %s:%s\n' "${status}" "${label}" >&2; failures=$((failures + 1)) ;;
        esac
    done
    if [[ $_saw_end -eq 0 ]]; then
        printf '  FAIL batch execution incomplete (no BATCH_END sentinel)\n' >&2
        failures=$((failures + 1))
    fi
}

echo "AstroAI image verification"
echo "=========================="

# ================================================================
# Batch 1 — ~39 checks: PATH + all command -v lookups + env var
# (replaces 39 individual login_shell calls). Skipped in --agents
# mode (lightweight probe) — it only exercises the agent verb surface.
# ================================================================
if [[ "${AGENTS}" -eq 0 ]]; then
process_batch < <(batch_login <<'CHECK_BATCH'
# PATH
[[ ":${PATH}:" == *":/opt/astroai/venv/cadc/bin:"* ]] && echo "PASS:astroai-profile on PATH" || echo "FAIL:astroai-profile on PATH"

# CADC + bundled CLIs
for t in canfar cadcget cadcput cadc-tap vcp cadc-get-cert astroai peek; do
    command -v "$t" >/dev/null 2>&1 && echo "PASS:login shell: ${t}" || echo "FAIL:login shell: ${t}"
done

# Tool ecosystem
for t in gh rg fd bat fzf hyperfine glow mdcat ov uv pixi micromamba mamba patch make file xxd hexdump lsof ss host ncdu shellcheck ctags \
         gcc g++ gfortran ld ar rustc cargo cmake ninja autoconf automake libtoolize flex bison; do
    command -v "$t" >/dev/null 2>&1 && echo "PASS:login shell: ${t}" || echo "FAIL:login shell: ${t}"
done

# Env
[[ -n "${ASTROAI_LAB_BIN_DIR:-}" ]] && echo "PASS:ASTROAI_LAB_BIN_DIR set" || echo "FAIL:ASTROAI_LAB_BIN_DIR set"
echo "BATCH_END"
CHECK_BATCH
)
fi

# ================================================================
# Batch 2 — astroai subcommands + WORK overlay policy. Skipped
# in --agents mode (lightweight probe).
# ================================================================
if [[ "${AGENTS}" -eq 0 ]]; then
process_batch < <(batch_login <<'CHECK_BATCH'
astroai status --json >/dev/null 2>&1 && echo "PASS:astroai status" || echo "FAIL:astroai status"
astroai env export --json | grep -q '"WORK"' && echo "PASS:astroai env export" || echo "FAIL:astroai env export"
astroai save --list --json >/dev/null 2>&1 && echo "PASS:astroai save --list" || echo "FAIL:astroai save --list"
astroai agent list >/dev/null 2>&1 && echo "PASS:astroai agent list" || echo "FAIL:astroai agent list"

# WORK relocate: /srcdir on the overlay (same device as /) + writable /scratch
# on another volume → $SCRATCH/src. Bind-mounted /srcdir must stay put.
# Mirrors astroai_lab.core.session_common.overlay_work_dir.
flag="$(printf '%s' "${ASTROAI_LAB_WORK_ON_SCRATCH:-}" | tr '[:upper:]' '[:lower:]')"
exported="$(astroai env export --json | python3 -c 'import json,sys; print(json.load(sys.stdin).get("WORK",""))' 2>/dev/null || true)"
case "${flag}" in
    0|false|no|off)
        echo "PASS:WORK relocate disabled"
        ;;
    *)
        scratch="${SCRATCH:-/scratch}"
        if [[ -d "${scratch}" && -w "${scratch}" ]]; then
            root_dev="$(stat -c %d / 2>/dev/null || true)"
            scratch_dev="$(stat -c %d "${scratch}" 2>/dev/null || true)"
            if [[ -d /srcdir ]]; then
                src_dev="$(stat -c %d /srcdir 2>/dev/null || true)"
            else
                src_dev="${root_dev}"
            fi
            if [[ -n "${root_dev}" && -n "${scratch_dev}" && "${scratch_dev}" != "${root_dev}" && "${src_dev}" == "${root_dev}" ]]; then
                expected="${scratch%/}/src"
                if [[ "${exported}" == "${expected}" && -d "${exported}" && -w "${exported}" ]]; then
                    echo "PASS:WORK overlay relocate"
                else
                    echo "FAIL:WORK overlay relocate (got ${exported:-empty}, want ${expected})"
                fi
            elif [[ "${exported}" == "${scratch%/}/src" && "${src_dev}" != "${root_dev}" ]]; then
                echo "FAIL:WORK relocated despite bind-mounted /srcdir"
            else
                echo "PASS:WORK no overlay relocate"
            fi
        else
            echo "PASS:WORK no writable scratch"
        fi
        ;;
esac
echo "BATCH_END"
CHECK_BATCH
)
fi

# Direct file-system checks (no login shell needed). Skipped in
# --agents mode (lightweight probe).
if [[ "${AGENTS}" -eq 0 ]]; then
check() {
    local label="$1"
    shift
    if "$@"; then
        printf '  ok  %s\n' "${label}"
    else
        printf '  FAIL %s\n' "${label}" >&2
        failures=$((failures + 1))
    fi
}

check "CADC venv writable" test -w /opt/astroai/venv/cadc
check "upgrade-cadc-tools helper" test -x /opt/astroai/bin/upgrade-cadc-tools.sh
check "peek helper" test -x /opt/astroai/bin/peek
fi

# ================================================================
# Non-quick checks — batched into ONE login shell with conditional
# logic inside (replaces ~17 individual login_shell calls). In
# --agents mode these are skipped entirely (lightweight probe).
# ================================================================
if [[ "${QUICK}" -eq 0 ]]; then
    if [[ "${AGENTS}" -eq 1 ]]; then
        # Lightweight post-push probe: agent verb surface only, no tool
        # installs — fast enough to gate every image push automatically.
        echo ""
        echo "Running agent verb-surface verification (--agents, no installs)..."
        /opt/astroai/bin/canfar-verify-agents.sh --setup || failures=$((failures + 1))
    else
        # Interactive shell is different from login shell; keep separate
        check "interactive shell: canfar" bash -ic 'command -v canfar >/dev/null' </dev/null

        process_batch < <(batch_login <<'CHECK_BATCH'
# cadcget / rg / file
canfar --help >/dev/null 2>&1 && echo "PASS:canfar CLI" || echo "FAIL:canfar CLI"
cadcget --help >/dev/null 2>&1 && echo "PASS:cadcget --help" || echo "FAIL:cadcget --help"
out=$(cadcget --version 2>&1); ! echo "$out" | grep -q SyntaxWarning && echo "PASS:cadcget --version (no SyntaxWarning)" || echo "FAIL:cadcget --version (no SyntaxWarning)"
rg --version >/dev/null 2>&1 && echo "PASS:rg search" || echo "FAIL:rg search"
file /bin/bash | grep -q ELF && echo "PASS:file magic" || echo "FAIL:file magic"

# node / npm (conditional on node being installed)
if command -v node >/dev/null 2>&1; then
    node --version >/dev/null 2>&1 && echo "PASS:node --version" || echo "FAIL:node --version"
    npm --version >/dev/null 2>&1 && echo "PASS:npm --version" || echo "FAIL:npm --version"
fi

# WORK (only report when the guard passes)
if [[ -n "${WORK:-}" && -d "${WORK}" && -w "${WORK}" ]]; then
    echo "PASS:WORK writable"
fi

# Scratch-mounted checks
if [[ -d "${SCRATCH}" && -w "${SCRATCH}" ]]; then
    u="${USER:-$(id -un)}"
    root="${SCRATCH}/.cache-${u}"
    [[ "${UV_CACHE_DIR}" == "${root}/"* ]] && echo "PASS:session cache root layout" || echo "FAIL:session cache root layout"
    for var in XDG_CACHE_HOME UV_CACHE_DIR PIXI_CACHE_DIR RATTLER_CACHE_DIR PIP_CACHE_DIR NPM_CONFIG_CACHE MAMBA_PKGS_DIRS CONDA_PKGS_DIRS; do
        [[ "${!var}" == "${root}" || "${!var}" == "${root}/"* ]] && echo "PASS:${var} under session cache root" || echo "FAIL:${var} under session cache root"
        [[ "${!var}" != "${HOME}" && "${!var}" != "${HOME}/"* ]] && echo "PASS:${var} off home" || echo "FAIL:${var} off home"
    done
    [[ "${ASTROAI_LAB_BIN_DIR}" == "${SCRATCH}/"* ]] && echo "PASS:ASTROAI_LAB_BIN_DIR on scratch" || echo "FAIL:ASTROAI_LAB_BIN_DIR on scratch"
    [[ "${ASTROAI_LAB_RUNTIME_ROOT}" == "${SCRATCH}/"* ]] && echo "PASS:ASTROAI_LAB_RUNTIME_ROOT on scratch" || echo "FAIL:ASTROAI_LAB_RUNTIME_ROOT on scratch"
    [[ "${UV_PYTHON_INSTALL_DIR}" != "${HOME}/"* ]] && echo "PASS:UV_PYTHON_INSTALL_DIR off home" || echo "FAIL:UV_PYTHON_INSTALL_DIR off home"
    [[ "${PIXI_HOME}" != "${HOME}/.pixi" ]] && echo "PASS:PIXI_HOME off home when scratch mounted" || echo "FAIL:PIXI_HOME off home when scratch mounted"
    astroai env export --no-ensure | grep -q ASTROAI_LAB_BIN_DIR && echo "PASS:astroai env export" || echo "FAIL:astroai env export"
elif [[ -n "${WORK:-}" ]]; then
    for var in XDG_CACHE_HOME UV_CACHE_DIR PIXI_CACHE_DIR RATTLER_CACHE_DIR PIP_CACHE_DIR NPM_CONFIG_CACHE MAMBA_PKGS_DIRS CONDA_PKGS_DIRS; do
        [[ "${!var}" == "${WORK}" || "${!var}" == "${WORK}/"* ]] && echo "PASS:${var} under WORK" || echo "FAIL:${var} under WORK"
        [[ "${!var}" != "${HOME}" && "${!var}" != "${HOME}/"* ]] && echo "PASS:${var} off home" || echo "FAIL:${var} off home"
    done
fi
echo "BATCH_END"
CHECK_BATCH
)

        echo ""
        echo "Running agent setup & install verification..."
        /opt/astroai/bin/canfar-verify-agents.sh || failures=$((failures + 1))
    fi
fi

echo ""
if [[ "${failures}" -eq 0 ]]; then
    echo "All checks passed."
    exit 0
fi
echo "${failures} check(s) failed." >&2
exit 1
