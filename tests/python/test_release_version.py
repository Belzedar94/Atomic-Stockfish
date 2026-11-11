import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Optional

import pyffish


ROOT = Path(__file__).resolve().parents[2]
# The release that the release workflows build (their RELEASE_VERSION). The
# source tree does not carry it: each build receives it as ATOMIC_VERSION.
EXPECTED = "1.0.3"
DEVELOPMENT_INFO = re.compile(r"Atomic-Stockfish dev-[0-9]{8}-(?:[0-9a-f]{8}|nogit) ")


def run_setup_version(atomic_version: Optional[str]) -> subprocess.CompletedProcess:
    environment = {
        name: value for name, value in os.environ.items() if name != "ATOMIC_VERSION"
    }
    if atomic_version is not None:
        environment["ATOMIC_VERSION"] = atomic_version
    return subprocess.run(
        [sys.executable, "setup.py", "--version"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def git_identity() -> Optional[tuple[str, str]]:
    """Return the commit date and short SHA that name a development build."""
    commands = (
        ["rev-parse", "--show-cdup"],
        ["rev-parse", "HEAD"],
        ["show", "-s", "--date=format:%Y%m%d", "--format=%cd", "HEAD"],
    )
    outputs = []
    for command in commands:
        try:
            completed = subprocess.run(
                ["git", "-C", str(ROOT), *command],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return None
        if completed.returncode != 0:
            return None
        outputs.append(completed.stdout.strip())
    if outputs[0] != "":
        return None
    return outputs[2], outputs[1][:8]


def test_source_tree_carries_no_release_number() -> None:
    header = (ROOT / "src" / "atomic_version.h").read_text(encoding="utf-8")
    declarations = [
        line.strip() for line in header.splitlines() if "AtomicVersionString =" in line
    ]
    assert declarations == [
        "inline constexpr std::string_view AtomicVersionString = "
        "ATOMIC_VERSION_STRINGIFY(ATOMIC_VERSION);",
        'inline constexpr std::string_view AtomicVersionString = "dev";',
    ]
    assert re.search(r"[0-9]+\.[0-9]+\.[0-9]+", header) is None

    package = json.loads((ROOT / "tests" / "js" / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == "0.0.0-dev"
    assert EXPECTED not in (ROOT / "setup.py").read_text(encoding="utf-8")

    recipe = (ROOT / "scripts" / "build_atomic_native_release.sh").read_text(encoding="utf-8")
    assert "AtomicVersionString" not in recipe
    assert 'ATOMIC_VERSION="$version"' in recipe


def test_setup_names_release_and_development_builds() -> None:
    release = run_setup_version(EXPECTED)
    assert release.returncode == 0, release.stderr
    assert release.stdout.strip() == EXPECTED

    development = run_setup_version(None)
    assert development.returncode == 0, development.stderr
    identity = git_identity()
    if identity is None:
        assert development.stdout.strip() == "0.0.0.dev0"
    else:
        assert development.stdout.strip() == "0.0.0.dev%s+g%s" % identity

    for malformed in ("1.0", "01.0.3", "1.0.3-rc1", "v1.0.3"):
        rejected = run_setup_version(malformed)
        assert rejected.returncode != 0
        assert "ATOMIC_VERSION must be MAJOR.MINOR.PATCH" in rejected.stderr


def test_compiled_python_surface_reports_its_build_version() -> None:
    module_path = Path(pyffish.__file__).resolve()
    assert module_path.parent == ROOT

    # The in-place module must have been built in this same environment: as the
    # release named by ATOMIC_VERSION, or else as a development build.
    release = os.environ.get("ATOMIC_VERSION")
    if release:
        assert pyffish.version() == tuple(int(part) for part in release.split("."))
        assert pyffish.info().startswith("Atomic-Stockfish " + release + " ")
        return

    assert pyffish.version() == (0, 0, 0)
    identity = git_identity()
    if identity is None:
        assert DEVELOPMENT_INFO.match(pyffish.info()), pyffish.info()
    else:
        assert pyffish.info().startswith("Atomic-Stockfish dev-%s-%s " % identity)


def test_native_incremental_build_tracks_the_version() -> None:
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    rule = makefile.split("misc.o: misc.cpp", 1)[1].split("\n\n", 1)[0]
    assert "atomic_version.h" in rule.splitlines()[0]
    assert "$(BUILD_VERSION_FILE)" in rule.splitlines()[0]
    assert "$(if $(ATOMIC_RELEASE),-DATOMIC_VERSION=$(ATOMIC_RELEASE))" in rule
    assert "_ := $(call cache_file_contents,$(BUILD_VERSION_FILE),$(ATOMIC_RELEASE))" in makefile
    assert "ATOMIC_VERSION must be MAJOR.MINOR.PATCH" in makefile
