#!/usr/bin/env bash
# Local smoke for the base image: every shipped CLI must run, not just exist.
# Usage: ./scripts/test-base-local.sh [tag]
set -euo pipefail

TAG="${1:-${BUILD_TAG:-local}}"
REGISTRY="${REGISTRY:-images.canfar.net}"
OWNER="${OWNER:-astroai}"
IMAGE="${REGISTRY}/${OWNER}/base:${TAG}"

echo "Testing ${IMAGE} (commands actually run)"

docker run --rm --entrypoint bash "${IMAGE}" -lc '
set -euo pipefail
source /etc/profile.d/astroai.sh 2>/dev/null || true
missing=0
tmpdir=$(mktemp -d -p /tmp)
trap "rm -rf \"$tmpdir\"" EXIT

pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; missing=$((missing + 1)); }

# Run a command; succeed if exit 0. Args after label are the command.
run() {
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then
        pass "$label"
    else
        fail "$label ($*)"
    fi
}

# --- presence + execute ---
# Tooling / shells / editors
run "bash --version" bash --version
run "python3 -c" python3 -c "print(42)"
run "git --version" git --version
run "delta --version" delta --version
run "git-lfs --version" git-lfs version
run "gh --version" gh --version
run "vim --version" vim --version
run "nano --version" nano --version
run "emacs --version" emacs --version
run "less --version" less --version
run "htop --version" htop --version
run "nvtop --version" nvtop --version
run "ncdu -v" ncdu -v
run "tree --version" tree --version
run "tldr --version" tldr --version

# Search / fuzzy / pager helpers
run "rg --version" rg --version
run "rg finds text" bash -c "printf \"a\\nb\\n\" | rg -q b"
run "fd --version" fd --version
run "fzf --version" fzf --version
run "bat --version" bat --version
run "bat pipes" bash -c "printf hi | bat --style=plain --paging=never | grep -q hi"
run "glow --version" glow --version
run "mdcat --version" mdcat --version
run "mdless --version" mdless --version
run "ov --version" ov --version

# Network / transfer
run "curl --version" curl --version
run "wget --version" wget --version
run "rsync --version" rsync --version
run "ssh -V" ssh -V
run "dig -v" dig -v
run "host localhost" host localhost
run "ip -V" ip -V
run "ss --version" ss --version

# Compression / archives
run "gzip --version" gzip --version
run "bzip2 --help" bzip2 --help
run "xz --version" xz --version
run "zstd --version" zstd --version
run "pigz --version" pigz --version
run "zip -v" zip -v
run "unzip -v" unzip -v
run "tar --version" tar --version

# Build toolchain
run "gcc --version" gcc --version
run "g++ --version" g++ --version
run "gfortran --version" gfortran --version
run "make --version" make --version
run "cmake --version" cmake --version
run "ninja --version" ninja --version
run "pkg-config --version" pkg-config --version
run "autoconf --version" autoconf --version
run "automake --version" automake --version
run "libtoolize --version" libtoolize --version
run "flex --version" flex --version
run "bison --version" bison --version
run "patch --version" patch --version
run "ar --version" ar --version
run "ld --version" ld --version
run "rustc --version" rustc --version
run "cargo --version" cargo --version
run "shellcheck --version" shellcheck --version
run "ctags --version" ctags --version
run "gcc hello" bash -c "printf \"%s\\n\" \"int main(void){return 0;}\" > \"$tmpdir/h.c\" && gcc -o \"$tmpdir/h\" \"$tmpdir/h.c\" && \"$tmpdir/h\""

# Data / inspect
run "jq --version" jq --version
run "jq parses" bash -c "printf \"{\\\"a\\\":1}\" | jq -e .a >/dev/null"
run "file --version" file --version
run "file magic" file /bin/bash
run "xxd -v" xxd -v
run "hexdump -V" hexdump -V
run "ps --version" ps --version
run "lsof -v" lsof -v
run "getfacl --version" getfacl --version
run "setfacl --version" setfacl --version
run "hyperfine --version" hyperfine --version

# Node + package managers
run "node --version" node --version
run "node eval" node -e "process.exit(0)"
run "npm --version" npm --version
run "npx --version" npx --version
run "uv --version" uv --version
run "uvx --version" uvx --version
run "pip --version" pip --version
run "pixi --version" pixi --version
run "micromamba --version" micromamba --version
run "mamba --version" mamba --version

# AstroAI / CADC (help/version only — no network auth)
run "astroai --help" astroai --help
run "astroai cluster --help" astroai cluster --help
run "astroai status --json" bash -c "astroai status --json >/dev/null"
run "astroai env export --json" bash -c "astroai env export --json --no-ensure | grep -q WORK"
run "canfar --help" canfar --help
run "cadcget --help" cadcget --help
run "cadcput --help" cadcput --help
run "cadc-tap --help" cadc-tap --help
run "vcp --help" vcp --help
run "vls --help" vls --help
run "cadc-get-cert --help" cadc-get-cert --help
run "peek -h" peek -h
run "peek reads file" bash -c "printf hi > \"$tmpdir/x.txt\" && peek \"$tmpdir/x.txt\" | grep -q hi"
run "cadcget no SyntaxWarning" bash -c "out=\$(cadcget --version 2>&1); ! echo \"\$out\" | grep -q SyntaxWarning"

# Markdown TUIs: render a tiny file without a TTY pager
printf "# hi\\n" > "$tmpdir/t.md"
run "glow render" glow --style=auto "$tmpdir/t.md"
run "mdcat render" mdcat "$tmpdir/t.md"

exit "$missing"
'

echo "base local smoke passed for ${IMAGE}"
