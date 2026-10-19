from __future__ import annotations

from email.parser import HeaderParser
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path


def fail(message: str) -> None:
    raise AssertionError(message)


def typecheck_installed_wheel(python: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--python-executable",
            str(python),
            "-m",
            "pyffish",
            "--no-incremental",
            "--no-error-summary",
        ],
        cwd=python.parent.parent,
        check=True,
    )


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: test_wheel_layout.py WHEEL_OR_DIRECTORY")

    requested = Path(sys.argv[1]).resolve()
    if requested.is_dir():
        wheels = sorted(requested.glob("*.whl"))
        if len(wheels) != 1:
            fail(f"expected exactly one wheel in {requested}, found {len(wheels)}")
        wheel = wheels[0]
    else:
        wheel = requested

    if not wheel.is_file():
        fail(f"wheel does not exist: {wheel}")

    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())
        metadata_files = [name for name in members if name.endswith(".dist-info/METADATA")]
        if len(metadata_files) != 1:
            fail("wheel does not contain exactly one METADATA file")
        metadata = HeaderParser().parsestr(archive.read(metadata_files[0]).decode("utf-8"))
    version = metadata["Version"]
    if not version:
        fail("wheel METADATA does not record a Version")

    if "pyffish.pyi" not in members:
        fail("wheel does not contain top-level pyffish.pyi")
    if "pyffish-stubs/__init__.pyi" not in members:
        fail("wheel does not contain the PEP 561 pyffish-stubs package")
    if any(name.endswith(".data/data/pyffish.pyi") for name in members):
        fail("wheel still installs pyffish.pyi through the data-files scheme")
    if not any(
        name.startswith("pyffish.") and name.endswith((".so", ".pyd"))
        for name in members
    ):
        fail("wheel does not contain the native pyffish extension")
    if not any(
        name.endswith(".dist-info/licenses/Copying.txt") for name in members
    ):
        fail("wheel does not contain the GPL license text")

    with tempfile.TemporaryDirectory(prefix="atomic-pyffish-wheel-") as directory:
        target = Path(directory)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                "--target",
                str(target),
                str(wheel),
            ],
            check=True,
        )
        if not (target / "pyffish.pyi").is_file():
            fail("installed wheel does not place pyffish.pyi at site-packages root")
        installed_extensions = [
            path
            for path in target.iterdir()
            if path.name.startswith("pyffish.") and path.suffix in {".so", ".pyd"}
        ]
        if not installed_extensions:
            fail("installed extension is not adjacent to pyffish.pyi")

        # The engine inside the wheel names the same build as the wheel: the
        # release it was made for, or the commit of a development build.
        probe = r"""
import pathlib
import re
import sys

sys.path.insert(0, sys.argv[1])
import pyffish

root = pathlib.Path(sys.argv[1]).resolve()
assert pathlib.Path(pyffish.__file__).resolve().parent == root
version = sys.argv[2]
release = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version)
development = re.fullmatch(r"0\.0\.0\.dev([0-9]{8})\+g([0-9a-f]{8})", version)
if release:
    assert pyffish.version() == tuple(int(part) for part in release.groups())
    assert pyffish.info().startswith("Atomic-Stockfish " + version + " ")
else:
    assert pyffish.version() == (0, 0, 0)
    name = (
        re.escape("dev-%s-%s" % development.groups())
        if development
        else r"dev-[0-9]{8}-(?:[0-9a-f]{8}|nogit)"
    )
    assert re.match("Atomic-Stockfish " + name + " ", pyffish.info()), pyffish.info()
assert pyffish.variants() == ["atomic"]
assert pyffish.validate_fen(
    "7k/8/8/8/8/8/8/K7 w - - not-a-number 1", "atomic"
) == pyffish.FEN_INVALID_HALF_MOVE_COUNTER
"""
        subprocess.run(
            [sys.executable, "-I", "-c", probe, str(target), version],
            cwd=target,
            check=True,
        )

        typecheck_environment = target / "typecheck-environment"
        venv.EnvBuilder(with_pip=True).create(typecheck_environment)
        if sys.platform == "win32":
            typecheck_python = typecheck_environment / "Scripts" / "python.exe"
        else:
            typecheck_python = typecheck_environment / "bin" / "python"

        subprocess.run(
            [
                str(typecheck_python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                str(wheel),
            ],
            check=True,
        )
        typecheck_installed_wheel(typecheck_python)

    print("Atomic pyffish wheel layout, import and PEP 561 discovery passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
