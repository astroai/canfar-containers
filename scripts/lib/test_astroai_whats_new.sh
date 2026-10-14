#!/usr/bin/env bash
# First interactive shell prints whats-new once; startup shells do not.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NEWS="${ROOT}/config/whats-new.txt"
SCRIPT="${ROOT}/scripts/astroai-whats-new.sh"

grep -q '^# id: 26.10-vos$' "${NEWS}"

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

# Non-interactive source (session startup, bash -lc) must not print or stamp.
out="$(
    HOME="${TMP}/quiet" ASTROAI_WHATS_NEW_FILE="${NEWS}" \
        bash --noprofile --norc -c "source '${SCRIPT}'"
)"
[[ -z "${out}" ]] || {
    echo "FAIL: non-interactive shell printed: ${out}" >&2
    exit 1
}
[[ ! -e "${TMP}/quiet/.astroai/lab/whats-new-seen" ]] || {
    echo "FAIL: non-interactive shell wrote the stamp" >&2
    exit 1
}

python3 - "${TMP}" "${NEWS}" "${SCRIPT}" <<'PY'
import os, pty, select, subprocess, sys

home_root, news, script = sys.argv[1:]

def run(home: str) -> str:
    master, slave = pty.openpty()
    env = os.environ.copy()
    env["HOME"] = home
    env["ASTROAI_WHATS_NEW_FILE"] = news
    env["TERM"] = "xterm-256color"
    env.pop("ASTROAI_WHATS_NEW", None)
    proc = subprocess.Popen(
        ["bash", "--noprofile", "--norc", "-i"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        close_fds=True,
    )
    os.close(slave)
    os.write(master, f"source {script}\nexit\n".encode())
    chunks = []
    while True:
        ready, _, _ = select.select([master], [], [], 3)
        if not ready:
            break
        try:
            data = os.read(master, 4096)
        except OSError:
            break
        if not data:
            break
        chunks.append(data)
    proc.wait(timeout=5)
    os.close(master)
    return b"".join(chunks).decode("utf-8", "replace")

first = run(f"{home_root}/first")
if "What's new in this AstroAI image" not in first:
    sys.stderr.write(f"FAIL: first shell missed the note:\n{first}\n")
    sys.exit(1)
if "# id:" in first:
    sys.stderr.write("FAIL: id line was shown to the user\n")
    sys.exit(1)
stamp = f"{home_root}/first/.astroai/lab/whats-new-seen"
with open(stamp, encoding="utf-8") as fh:
    seen = fh.read().strip()
if seen != "26.10-vos":
    sys.stderr.write(f"FAIL: stamp is {seen!r}\n")
    sys.exit(1)

second = run(f"{home_root}/first")
if "What's new in this AstroAI image" in second:
    sys.stderr.write(f"FAIL: second shell repeated the note:\n{second}\n")
    sys.exit(1)

master, slave = pty.openpty()
env = os.environ.copy()
env["HOME"] = f"{home_root}/off"
env["ASTROAI_WHATS_NEW_FILE"] = news
env["ASTROAI_WHATS_NEW"] = "0"
env["TERM"] = "xterm-256color"
proc = subprocess.Popen(
    ["bash", "--noprofile", "--norc", "-i"],
    stdin=slave,
    stdout=slave,
    stderr=slave,
    env=env,
    close_fds=True,
)
os.close(slave)
os.write(master, f"source {script}\nexit\n".encode())
chunks = []
while True:
    ready, _, _ = select.select([master], [], [], 3)
    if not ready:
        break
    try:
        data = os.read(master, 4096)
    except OSError:
        break
    if not data:
        break
    chunks.append(data)
proc.wait(timeout=5)
os.close(master)
silenced = b"".join(chunks).decode("utf-8", "replace")
if "What's new in this AstroAI image" in silenced:
    sys.stderr.write("FAIL: ASTROAI_WHATS_NEW=0 still printed\n")
    sys.exit(1)
if os.path.exists(f"{home_root}/off/.astroai/lab/whats-new-seen"):
    sys.stderr.write("FAIL: silenced shell wrote the stamp\n")
    sys.exit(1)
print("PASS: whats-new once per id")
PY
