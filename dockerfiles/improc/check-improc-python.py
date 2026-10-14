#!/usr/bin/env python3
"""One science interpreter, one CUDA 12.9 toolkit.

Do not require a GPU device. The image build has no driver, so
torch.cuda.is_available() and a JAX device are not asserted.
"""

from __future__ import annotations

import glob
import importlib
import os
import sys
import sysconfig


def fail(message: str) -> None:
    sys.exit(f"FAIL: {message}")


def main() -> None:
    if os.path.exists("/opt/astroai/conda"):
        fail("/opt/astroai/conda exists")

    sp = sysconfig.get_path("purelib")
    for dist in ("nvidia_cublas_cu12", "nvidia_cudnn_cu12", "nvidia_nccl_cu12"):
        hits = glob.glob(os.path.join(sp, f"{dist}-*.dist-info"))
        if len(hits) != 1:
            fail(f"expected one {dist} distribution, found {hits}")
    # CUDA 13 shows up as nvidia-*-cu13 or as unsuffixed nvidia-* 13.x wheels.
    extra_cuda = sorted(
        name
        for name in os.listdir(sp)
        if name.startswith("nvidia_")
        and name.endswith(".dist-info")
        and "_cu12-" not in name
    )
    if extra_cuda:
        fail(f"NVIDIA packages outside CUDA 12: {extra_cuda}")

    import torch

    if "+cu" not in torch.__version__:
        fail(f"torch is not a CUDA build: {torch.__version__}")

    import tensorflow as tf

    if not tf.test.is_built_with_cuda():
        fail(f"tensorflow is not a CUDA build: {tf.__version__}")

    plugin_dists = glob.glob(os.path.join(sp, "jax_cuda12_plugin-*.dist-info"))
    if not plugin_dists:
        fail("jax-cuda12-plugin is not installed")

    imported = False
    errors: list[str] = []
    for name in ("jax_cuda12_plugin", "jax_plugins.xla_cuda12"):
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 — report the loader error
            text = f"{type(exc).__name__}: {exc}"
            errors.append(f"{name}: {text}")
            if "libcuda.so" in text:
                print(f"{name} is installed; loading it needs the NVIDIA driver")
                imported = True
                break
        else:
            print(f"imported {name}")
            imported = True
            break
    if not imported:
        fail("jax cuda12 plugin did not import: " + " | ".join(errors))

    import ngmix

    print(
        "PASS: one CUDA 12.9",
        torch.__version__,
        "ngmix",
        ngmix.__file__,
    )


if __name__ == "__main__":
    main()
