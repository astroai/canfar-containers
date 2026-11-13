#!/usr/bin/env bash
# Assert the image CPython and optional venv share ASTROAI_PYTHON_VERSION.
# Usage: assert-image-python.sh [/path/to/venv/bin/python ...]
set -euo pipefail

want="${ASTROAI_PYTHON_VERSION:?ASTROAI_PYTHON_VERSION is unset}"
sys_ver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if [[ "${sys_ver}" != "${want}" ]]; then
  echo "FAIL: system python is ${sys_ver}, want ${want}" >&2
  exit 1
fi

for py in "$@"; do
  ver="$("${py}" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  if [[ "${ver}" != "${want}" ]]; then
    echo "FAIL: ${py} is Python ${ver}, want ${want}" >&2
    exit 1
  fi
  # Refuse a uv-managed copy: venv must resolve to the image interpreter family.
  home="$("${py}" -c 'import sys; print(sys.base_prefix)')"
  case "${home}" in
    /usr/local|/usr/local/*) ;;
    *)
      echo "FAIL: ${py} base_prefix=${home} (expected image /usr/local Python)" >&2
      exit 1
      ;;
  esac
done

echo "PASS: python ${want} (system${*:+ + $*})"
