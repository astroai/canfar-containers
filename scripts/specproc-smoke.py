"""Import smoke for the specproc image. One name per line on failure."""
import importlib
import sys

MODS = [
    "specutils",
    "specreduce",
    "pyspeckit",
    "spectres",
    "lmfit",
    "ppxf",
    "pysme",
    "fsps",
    "prospect",
    "sedpy",
    "synple",
    "synspec",
    "turbospectrum",
    "doppler",
    "thecannon",
    "fraunhofer",
    "roland",
    "starlyte",
    "moogpy",
    "pymoog",
    "astroARIADNE",
    "atlas_py",
    "synthe_py",
    "agama",
    "vice",
]


def _check_agama() -> str | None:
    try:
        import agama

        agama.Potential(type="Plummer", mass=1.0, scaleRadius=1.0)
    except Exception as exc:
        return f"agama: {exc.__class__.__name__}: {exc}"
    return None


def _check_vice() -> str | None:
    try:
        import vice

        vice.singlezone()
    except Exception as exc:
        return f"vice: {exc.__class__.__name__}: {exc}"
    return None


def _check_innterpol() -> str | None:
    """Weights are in the checkout. Running them needs torch, which is not baked."""
    import pathlib

    root = pathlib.Path("/opt/astroai/iNNterpol")
    needed = (
        root / "2021-07-24-20:01:55.pth",
        root / "iNNterpol_MARCS",
        root / "iNNterpol_PHOENIX",
    )
    missing = [str(path) for path in needed if not path.exists()]
    if missing:
        return "innterpol: missing " + ", ".join(missing)
    return None


def main() -> int:
    bad: list[str] = []
    for name in MODS:
        try:
            importlib.import_module(name)
        except Exception as exc:
            bad.append(f"{name}: {exc.__class__.__name__}: {exc}")
    inn = _check_innterpol()
    if inn:
        bad.append(inn)
    for check in (_check_agama, _check_vice):
        err = check()
        if err:
            bad.append(err)
    if bad:
        print("import failures:", file=sys.stderr)
        print("\n".join(bad), file=sys.stderr)
        return 1
    print("specproc smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
