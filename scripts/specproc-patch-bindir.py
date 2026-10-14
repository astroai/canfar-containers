"""Point Nidever setup.py bin discovery at the specproc venv.

Those setup.py files parse setuptools' --user help text and then treat a
missing macOS path as False, so the copy step crashes on Linux. Also drop
setup_requires so setuptools does not fetch a broken setuptools_scm egg.
"""
from __future__ import annotations

import sys
from pathlib import Path

BIND = "/opt/astroai/venv/specproc/bin"


def patch_setup(path: Path) -> None:
    text = path.read_text()
    old = "def get_bin_path():"
    new = (
        "def get_bin_path():\n"
        f'    return "{BIND}"\n'
        "\n"
        "def _get_bin_path_unused():"
    )
    if old not in text:
        raise SystemExit(f"{path} has no get_bin_path()")
    path.write_text(text.replace(old, new, 1))


def strip_setup_requires(path: Path) -> None:
    if not path.is_file():
        return
    kept: list[str] = []
    skipping = False
    for line in path.read_text().splitlines(True):
        if line.strip() == "setup_requires =":
            skipping = True
            continue
        if skipping:
            if line.startswith((" ", "\t")):
                continue
            skipping = False
        kept.append(line)
    path.write_text("".join(kept))


def main() -> None:
    setup = Path(sys.argv[1])
    patch_setup(setup)
    strip_setup_requires(setup.with_name("setup.cfg"))


if __name__ == "__main__":
    main()
