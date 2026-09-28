from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[2] / "config" / "ipython_kernel_config.py"


def _kernel_env(home: Path, **env: str) -> dict[str, str]:
    code = (
        "import os, runpy, json\n"
        f"runpy.run_path({str(CONFIG)!r})\n"
        "print(json.dumps({k: v for k, v in os.environ.items() if k.startswith('ASTRO_')}))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        env={"HOME": str(home), "PATH": os.environ.get("PATH", ""), **env},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    import json

    return json.loads(out)


def test_new_kernel_sees_keys_saved_after_boot(tmp_path: Path) -> None:
    keys = tmp_path / ".astroai" / "lab" / ".env"
    keys.parent.mkdir(parents=True)
    keys.write_text(
        "# comment\nASTRO_NEW=fresh\nASTRO_CHANGED='replaced'\nASTRO_EMPTY=\nnot a line\n",
        encoding="utf-8",
    )
    env = _kernel_env(tmp_path, ASTRO_CHANGED="stale-from-boot", ASTRO_OTHER="kept")
    assert env == {"ASTRO_NEW": "fresh", "ASTRO_CHANGED": "replaced", "ASTRO_OTHER": "kept"}


def test_missing_keys_file_is_harmless(tmp_path: Path) -> None:
    assert _kernel_env(tmp_path, ASTRO_OTHER="kept") == {"ASTRO_OTHER": "kept"}
