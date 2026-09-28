# Installed as /etc/ipython/ipython_kernel_config.py; IPython runs it inside every
# new kernel before the kernel starts. Kernels inherit JupyterLab's environment
# from session boot, so model API keys saved later (Studio hub or
# `canfar-lab agent keys set`) would otherwise only reach kernels after a restart
# of the whole session. Same NAME=value format canfar-lab writes.
import os as _os
from pathlib import Path as _Path


def _astroai_load_keys() -> None:
    try:
        text = (_Path.home() / ".astroai" / "lab" / ".env").read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip("'\"")
        if name.isidentifier() and value:
            _os.environ[name] = value


_astroai_load_keys()
