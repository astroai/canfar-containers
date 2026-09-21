#!/bin/bash
# AstroAI is keys-only: any provider key works (Settings → Models). Upstream dsh
# still opens a mandatory-looking "Add an API key to get started" dialog that
# only accepts the official DeepSeek credential. Treat that readiness state as
# unavailable so onboarding auto-completes (same as "Configure later").
set -euo pipefail

root="${1:-/opt/astroai}"
target="$(find "${root}" -path '*/dsh-client-ui-settings-models/lib/client.js' 2>/dev/null | head -1 || true)"
if [[ -z "${target}" || ! -f "${target}" ]]; then
    echo "dsh-client-ui-settings-models client.js not found under ${root}" >&2
    exit 1
fi

python3 - "${target}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
needle = 'return { kind: "credential-missing" };'
# Idempotent: already patched.
if "astroai-keys-only" in text:
    print(f"already patched: {path}")
    raise SystemExit(0)
if needle not in text:
    raise SystemExit(f"onboarding readiness needle not found in {path}")
# Keep a single replacement — onboardingReadiness is the only site of this
# exact return in current dsh builds.
replacement = (
    'return { kind: "unavailable", reason: "astroai-keys-only" }; '
    '/* AstroAI: skip DeepSeek-only first-run API key gate */'
)
path.write_text(text.replace(needle, replacement, 1), encoding="utf-8")
print(f"patched DeepSeek onboarding skip: {path}")
PY
