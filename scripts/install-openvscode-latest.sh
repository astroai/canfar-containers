#!/bin/bash
# Install the current gitpod-io/openvscode-server GitHub release.
# The Dockerfile ADDs releases/latest so this layer tracks that release.
# Upstream publishes the linux tarballs and no checksum asset.
set -euo pipefail
meta="${1:-/tmp/openvscode-release.json}"
test -s "$meta"
eval "$(python3 - "$meta" <<'PY'
import json, os, shlex, sys
rel = json.load(open(sys.argv[1]))
tag = rel["tag_name"]
arch = {"x86_64": "x64", "aarch64": "arm64"}.get(os.uname().machine)
if not arch:
    raise SystemExit(f"unsupported arch: {os.uname().machine}")
name = f"{tag}-linux-{arch}.tar.gz"
asset = next((a for a in rel["assets"] if a["name"] == name), None)
if asset is None:
    names = ", ".join(a["name"] for a in rel["assets"])
    raise SystemExit(f"release {tag} has no {name} (assets: {names})")
print(f"TAG={shlex.quote(tag)}")
print(f"ARCH={shlex.quote(arch)}")
print(f"URL={shlex.quote(asset['browser_download_url'])}")
print(f"NAME={shlex.quote(name)}")
PY
)"
curl -fsSL -o "/tmp/${NAME}" "${URL}"
rm -rf /opt/openvscode-server
tar -xzf "/tmp/${NAME}" -C /opt
mv "/opt/${TAG}-linux-${ARCH}" /opt/openvscode-server
rm -f "/tmp/${NAME}"
chmod -R a+rx /opt/openvscode-server
