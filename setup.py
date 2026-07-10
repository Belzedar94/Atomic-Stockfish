from __future__ import annotations

import platform
from pathlib import Path

from setuptools import Extension, setup


ROOT = Path(__file__).parent

SOURCES = [
    "src/pyffish.cpp",
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
    ]
else:
    compile_args = [
        "-std=c++17",
        "-O3",
        "-fvisibility=hidden",
        "-Wno-date-time",
    ]

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
    define_macros=define_macros,
    extra_compile_args=compile_args,
    libraries=["shell32"] if platform.system() == "Windows" else [],
    py_limited_api=True,
)

setup(
    name="atomic-pyffish",
    version="0.1.0",
    description="Stable-ABI Python rules binding for Atomic-Stockfish",
    long_description=(ROOT / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    license="GPL-3.0-or-later",
    python_requires=">=3.9",
    ext_modules=[pyffish],
    data_files=[("", ["pyffish.pyi"])],
    options={"bdist_wheel": {"py_limited_api": "cp39"}},
    classifiers=[
        "Development Status :: 3 - Alpha",
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
        "Programming Language :: C++",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Operating System :: Microsoft :: Windows",
        "Operating System :: POSIX :: Linux",
    ],
)
