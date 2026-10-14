#!/bin/bash
# sourcextractor++ links the image libpython, which does not see the science
# venv. The real binary stays in its InstallArea so its RPATH keeps working.
set -euo pipefail

ROOT=/opt/astroai/astrorama
shopt -s nullglob
py_dirs=( "$ROOT"/*/InstallArea/*/python )
bins=( "$ROOT"/SourceXtractorPlusPlus/InstallArea/*/bin/sourcextractor++ )
site=( /opt/astroai/venv/improc/lib/python*/site-packages )
shopt -u nullglob

if [[ ${#bins[@]} -ne 1 || ! -x "${bins[0]}" ]]; then
    echo "sourcextractor++ binary missing under ${ROOT}" >&2
    exit 127
fi
if [[ ${#site[@]} -ne 1 ]]; then
    echo "science venv site-packages missing" >&2
    exit 127
fi
IFS=:
if [[ ${#py_dirs[@]} -gt 0 ]]; then
    export PYTHONPATH="${py_dirs[*]}:${site[0]}${PYTHONPATH:+:$PYTHONPATH}"
else
    export PYTHONPATH="${site[0]}${PYTHONPATH:+:$PYTHONPATH}"
fi
unset IFS

exec "${bins[0]}" "$@"
