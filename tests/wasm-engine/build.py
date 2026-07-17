#!/usr/bin/env python3
"""Cross-platform builder for the complete Node UCI/NNUE WASM artifact.

The PowerShell entrypoint remains the normative local Windows wrapper.  CI
invokes this module inside the digest-pinned Emscripten container.  Both paths
consume the same source inventory from ``build.ps1`` so adding a backend cannot
silently update one build graph but not the other.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
SOURCE_ROOT = REPO_ROOT / "src"
SOURCE_LIST = re.compile(
    r"\$relativeSources\s*=\s*@\((?P<body>.*?)^\)", re.MULTILINE | re.DOTALL
)
SINGLE_QUOTED = re.compile(r"'([^']+)'")
SOURCE_DATE_EPOCH_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def parse_sources() -> list[Path]:
    powershell = (SCRIPT_DIR / "build.ps1").read_text(encoding="utf-8")
    match = SOURCE_LIST.search(powershell)
    if match is None:
        raise RuntimeError("could not locate $relativeSources in build.ps1")

    relative = SINGLE_QUOTED.findall(match.group("body"))
    if not relative or len(relative) != len(set(relative)):
        raise RuntimeError("WASM source inventory is empty or contains duplicates")

    sources = [(SOURCE_ROOT / item).resolve() for item in relative]
    missing = [str(path) for path in sources if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing WASM engine sources: " + ", ".join(missing))
    return sources


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest().lower()


def normalized_source_date_epoch(environment: dict[str, str]) -> str:
    """Return the caller's frozen epoch, with zero as the local default."""

    value = environment.get("SOURCE_DATE_EPOCH", "0")
    if SOURCE_DATE_EPOCH_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "SOURCE_DATE_EPOCH must be zero or a canonical positive decimal integer"
        )
    return value


def build(out_dir: Path, compiler: str, debug: bool) -> None:
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    sources = parse_sources()
    output_js = out_dir / "atomic-stockfish-nnue.js"
    uci_only = (SCRIPT_DIR / "uci_only.h").resolve()
    prefix_map = SOURCE_ROOT.resolve().as_posix()

    flags = [
        "-std=c++17",
        "-fexceptions",
        "-pthread",
        "-msimd128",
        "-msse2",
        "-msse3",
        "-mssse3",
        "-msse4.1",
        "-DNO_TABLEBASES",
        "-DNO_PREFETCH",
        "-DNNUE_EMBEDDING_OFF",
        "-DUSE_POPCNT",
        "-DUSE_PTHREADS",
        "-DUSE_SSE2",
        "-DUSE_SSSE3",
        "-DUSE_SSE41",
        "-DUSE_SLOPPY_ATOMICS",
        "-include",
        str(uci_only),
        f"-ffile-prefix-map={prefix_map}=.",
        f"-fdebug-prefix-map={prefix_map}=.",
        "-sENVIRONMENT=node",
        "-sNODERAWFS=1",
        "-sFORCE_FILESYSTEM=1",
        "-sINITIAL_MEMORY=536870912",
        "-sSTACK_SIZE=8388608",
        "-sPTHREAD_POOL_SIZE=4",
        "-sEXIT_RUNTIME=1",
        "-sNO_EXIT_RUNTIME=0",
        "-sDISABLE_EXCEPTION_CATCHING=0",
        "-sWASM_BIGINT=1",
        "-sDETERMINISTIC=1",
    ]
    if debug:
        flags.extend(
            [
                "-O1",
                "-g3",
                "-sASSERTIONS=2",
                "-sSAFE_HEAP=1",
                "-sSTACK_OVERFLOW_CHECK=2",
            ]
        )
    else:
        flags.extend(["-O3", "-flto", "-DNDEBUG", "-sASSERTIONS=0"])

    environment = os.environ.copy()
    source_date_epoch = normalized_source_date_epoch(environment)
    environment["SOURCE_DATE_EPOCH"] = source_date_epoch
    command = [compiler, *flags, *(str(source) for source in sources), "-o", str(output_js)]
    print(f"Building complete Atomic-Stockfish NNUE WASM in {out_dir}", flush=True)
    subprocess.run(command, check=True, env=environment)

    required = (output_js, out_dir / "atomic-stockfish-nnue.wasm")
    for artifact in required:
        if not artifact.is_file():
            raise FileNotFoundError(f"expected WASM artifact was not produced: {artifact}")

    wrapper = out_dir / "atomic-stockfish-nnue-node.mjs"
    shutil.copyfile(SCRIPT_DIR / "node-uci-wrapper.mjs", wrapper)
    artifacts = []
    for artifact in sorted(out_dir.glob("atomic-stockfish-nnue*")):
        if artifact.is_file():
            artifacts.append(
                {
                    "name": artifact.name,
                    "bytes": artifact.stat().st_size,
                    "sha256": sha256(artifact),
                }
            )

    compiler_line = subprocess.run(
        [compiler, "--version"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout.splitlines()[0]
    manifest = {
        "schemaVersion": 2,
        "target": "node-uci-nnue",
        "sourceDateEpoch": int(source_date_epoch),
        "debug": debug,
        "compiler": compiler_line,
        "initialMemoryBytes": 536870912,
        "memoryGrowth": False,
        "pthreadPoolSize": 4,
        "supportedEntrypoint": "atomic-stockfish-nnue-node.mjs",
        "generatedRuntimeGlue": "atomic-stockfish-nnue.js",
        "directRuntimeGlueSupported": False,
        "supportedNetworkBackends": [
            "Legacy Atomic V1",
            "AtomicNNUEV2",
            "AtomicNNUEV3",
        ],
        "networkFileVersions": ["0x7AF32F20", "0xA70C0002", "0xA70C0003"],
        "stdinPump": {
            "command": "isready",
            "response": "readyok",
            "intervalMilliseconds": 25,
            "maxOutstandingPrivatePumps": 1,
            "preservesUserReadyok": True,
        },
        "externalNetwork": True,
        "artifacts": artifacts,
    }
    with (out_dir / "manifest.json").open(
        "w", encoding="utf-8", newline="\n"
    ) as manifest_stream:
        manifest_stream.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

    for artifact in artifacts:
        print(
            f"  {artifact['name']}  {artifact['bytes']} bytes  "
            f"sha256={artifact['sha256']}"
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "build/wasm-engine")
    parser.add_argument("--compiler", default="em++")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    build(args.out_dir, args.compiler, args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
