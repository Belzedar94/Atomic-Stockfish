from __future__ import annotations

import platform
from pathlib import Path

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext


ROOT = Path(__file__).parent


def release_version() -> str:
    header = (ROOT / "src" / "atomic_version.h").read_text(encoding="utf-8")
    components = []
    for name in ("Major", "Minor", "Patch"):
        marker = f"AtomicVersion{name} = "
        line = next((line for line in header.splitlines() if marker in line), None)
        if line is None:
            raise RuntimeError(f"missing {marker.strip()} in atomic_version.h")
        value = line.split(marker, 1)[1].split(";", 1)[0].strip()
        if not value.isascii() or not value.isdecimal():
            raise RuntimeError(f"invalid AtomicVersion{name} in atomic_version.h")
        components.append(value)
    return ".".join(components)


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
        # MSVC otherwise hashes the absolute translation-unit path into
        # anonymous-namespace symbols, making equivalent isolated wheel
        # builds differ solely because cibuildwheel uses fresh temp roots.
        f"/d1trimfile:{ROOT.resolve()}\\",
    ]
    link_args = ["/Brepro"]
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
    version=release_version(),
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
