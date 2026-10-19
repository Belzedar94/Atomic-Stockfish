from __future__ import annotations

from email.parser import HeaderParser
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Optional

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext


ROOT = Path(__file__).parent
RELEASE_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
DEV_VERSION = re.compile(r"0\.0\.0\.dev([0-9]{8})\+g([0-9a-f]{8})\Z")


def git(*arguments: str) -> Optional[str]:
    """Return the output of a git command run in this checkout, if it succeeds."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(ROOT), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def build_version() -> tuple[str, list[tuple[str, str]]]:
    """Return the package version and the macros that name this build.

    Release builds set ATOMIC_VERSION=MAJOR.MINOR.PATCH. An sdist keeps the
    version it was made with in PKG-INFO, so wheels built from it report the
    same one. Any other build is a development build and names its commit, as
    the native Makefile does (see src/atomic_version.h).
    """
    requested = os.environ.get("ATOMIC_VERSION", "")
    if requested and RELEASE_VERSION.match(requested) is None:
        raise RuntimeError(f"ATOMIC_VERSION must be MAJOR.MINOR.PATCH, got {requested!r}")

    pkg_info = ROOT / "PKG-INFO"
    if pkg_info.is_file():
        recorded = HeaderParser().parsestr(pkg_info.read_text(encoding="utf-8"))["Version"]
        if not recorded:
            raise RuntimeError("PKG-INFO does not record a Version")
        if requested and requested != recorded:
            raise RuntimeError(f"ATOMIC_VERSION={requested} differs from this sdist ({recorded})")
        if RELEASE_VERSION.match(recorded):
            return recorded, [("ATOMIC_VERSION", recorded)]
        development = DEV_VERSION.match(recorded)
        if development is None:
            return recorded, []
        return recorded, [("GIT_DATE", development[1]), ("GIT_SHA", development[2])]

    if requested:
        return requested, [("ATOMIC_VERSION", requested)]

    # Only this repository's own commit names the build, not an enclosing one.
    sha = git("rev-parse", "HEAD") if git("rev-parse", "--show-cdup") == "" else None
    date = git("show", "-s", "--date=format:%Y%m%d", "--format=%cd", "HEAD") if sha else None
    if (
        sha is None
        or date is None
        or re.fullmatch(r"[0-9a-f]{40}", sha) is None
        or re.fullmatch(r"[0-9]{8}", date) is None
    ):
        return "0.0.0.dev0", []
    return f"0.0.0.dev{date}+g{sha[:8]}", [("GIT_DATE", date), ("GIT_SHA", sha[:8])]


VERSION, VERSION_MACROS = build_version()


class BuildExtWithStub(build_ext):
    """Install both the adjacent stub and its PEP 561 stub-only package."""

    def run(self) -> None:
        super().run()
        stub_package = Path(self.build_lib) / "pyffish-stubs"
        self.mkpath(str(stub_package))
        self.copy_file(
            str(ROOT / "pyffish.pyi"),
            str(Path(self.build_lib) / "pyffish.pyi"),
        )
        self.copy_file(
            str(ROOT / "pyffish.pyi"),
            str(stub_package / "__init__.pyi"),
        )


SOURCES = [
    "src/pyffish.cpp",
    "src/atomic_init.cpp",
    "src/attacks.cpp",
    "src/bitboard.cpp",
    "src/misc.cpp",
    "src/movegen.cpp",
    "src/position.cpp",
    "src/uci_move.cpp",
    "src/syzygy/tbprobe.cpp",
    "src/api/atomic_board.cpp",
    "src/api/atomic_fen.cpp",
    "src/api/atomic_notation.cpp",
    "src/api/atomic_outcome.cpp",
]

HEADERS = [
    str(path.relative_to(ROOT))
    for path in (ROOT / "src").rglob("*.h")
]

if platform.python_compiler().startswith("MSC"):
    compile_args = [
        "/std:c++17",
        "/O2",
        "/EHsc",
        "/permissive-",
        "/utf-8",
        "/Zc:__cplusplus",
        # setuptools enables /GL and /LTCG for optimized extensions.  The LTCG
        # scheduler is not byte-reproducible across every clean hosted build,
        # so override both defaults while retaining ordinary /O2 optimization.
        "/GL-",
        "/experimental:deterministic",
        # MSVC otherwise hashes the absolute translation-unit path into
        # anonymous-namespace symbols, making equivalent isolated wheel
        # builds differ solely because cibuildwheel uses fresh temp roots.
        f"/d1trimfile:{ROOT.resolve()}\\",
    ]
    link_args = ["/Brepro", "/LTCG:OFF"]
else:
    compile_args = [
        "-std=c++17",
        "-O3",
        "-fvisibility=hidden",
        "-Wno-date-time",
    ]
    link_args = []

define_macros = [
    ("Py_LIMITED_API", "0x03090000"),
    ("NO_TABLEBASES", "1"),
    ("NO_PREFETCH", "1"),
    ("NNUE_EMBEDDING_OFF", "1"),
    *VERSION_MACROS,
]
if platform.architecture()[0] == "64bit" and not platform.python_compiler().startswith("MSC"):
    define_macros.append(("IS_64BIT", "1"))

pyffish = Extension(
    "pyffish",
    sources=SOURCES,
    depends=HEADERS,
    include_dirs=[
        str(ROOT / "src"),
        str(ROOT / "src" / "api"),
        str(ROOT / "src" / "syzygy"),
    ],
    define_macros=define_macros,
    extra_compile_args=compile_args,
    extra_link_args=link_args,
    libraries=["shell32"] if platform.system() == "Windows" else [],
    py_limited_api=True,
)

setup(
    name="atomic-pyffish",
    version=VERSION,
    description="Stable-ABI Python rules binding for Atomic-Stockfish",
    long_description=(ROOT / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    author="The Atomic-Stockfish developers",
    url="https://github.com/Belzedar94/Atomic-Stockfish",
    project_urls={
        "Source": "https://github.com/Belzedar94/Atomic-Stockfish",
        "Issues": "https://github.com/Belzedar94/Atomic-Stockfish/issues",
        "Releases": "https://github.com/Belzedar94/Atomic-Stockfish/releases",
    },
    license="GPL-3.0-or-later",
    license_files=["Copying.txt"],
    python_requires=">=3.9",
    ext_modules=[pyffish],
    cmdclass={"build_ext": BuildExtWithStub},
    options={"bdist_wheel": {"py_limited_api": "cp39"}},
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Programming Language :: C++",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Operating System :: Microsoft :: Windows",
        "Operating System :: POSIX :: Linux",
    ],
)
