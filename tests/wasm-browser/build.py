#!/usr/bin/env python3
"""Builder for the complete Atomic-Stockfish browser Web Worker artifact.

This is a separate target from the Node UCI/NNUE build in
``tests/wasm-engine`` and from the lightweight Board WASM (``src/Makefile_js``).
It compiles the same complete engine source inventory as the Node build (read
from ``tests/wasm-engine/build.ps1`` through ``tests/wasm-engine/build.py``),
with ``main.cpp`` replaced by ``src/wasm/browser_main.cpp``, which exposes
``atomic_uci(const char*)`` and forwards output to Emscripten callbacks.

Outputs, in ``--out-dir``:

  atomic-stockfish.mjs        Emscripten ES-module runtime (factory
                              ``createAtomicStockfish``); also the script of
                              every pthread worker.
  atomic-stockfish.wasm       The engine. The NNUE network is not embedded.
  atomic-stockfish.worker.js  The public Web Worker (message protocol, NNUE
                              download and SHA-256 verification).
  manifest.json               Build provenance, flags, sizes and checksums.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
SOURCE_ROOT = REPO_ROOT / "src"
NODE_BUILD = REPO_ROOT / "tests" / "wasm-engine" / "build.py"

# The Emscripten release this target is built and tested with. It is the same
# release as the digest-pinned CI image used for the other WASM targets.
REQUIRED_EMSCRIPTEN = "4.0.10"

INITIAL_MEMORY = 536870912
PTHREAD_POOL_SIZE = 4
STACK_SIZE = 8388608
# The browser worker refuses Hash values that would not fit next to the network
# and the search threads in the fixed, non-growable WASM memory. 256 MiB fits a
# fresh engine but not after repeated Threads changes (heap fragmentation);
# 192 MiB was the largest value that survived them, so 128 MiB keeps a margin.
MAX_HASH_MIB = 128
MAX_NETWORK_BYTES = 134217728

ARTIFACT_NAMES = (
    "atomic-stockfish.mjs",
    "atomic-stockfish.wasm",
    "atomic-stockfish.worker.js",
)


def load_node_build():
    spec = importlib.util.spec_from_file_location("atomic_wasm_engine_build", NODE_BUILD)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {NODE_BUILD}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def browser_sources(node_build) -> list[Path]:
    """The complete Node engine inventory with the browser entry point."""

    sources = node_build.parse_sources()
    main = (SOURCE_ROOT / "main.cpp").resolve()
    if main not in sources:
        raise RuntimeError("main.cpp is missing from the WASM engine source inventory")
    entry = (SOURCE_ROOT / "wasm" / "browser_main.cpp").resolve()
    return [entry if source == main else source for source in sources]


def compile_flags(debug: bool) -> list[str]:
    flags = [
        "-std=c++17",
        "-fno-exceptions",
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
        "-DATOMIC_BROWSER_WORKER",
    ]
    if debug:
        flags.extend(["-O1", "-g3"])
    else:
        flags.extend(["-O3", "-flto", "-DNDEBUG"])
    return flags


def link_flags(debug: bool) -> list[str]:
    flags = [
        "-sENVIRONMENT=worker",
        "-sMODULARIZE=1",
        "-sEXPORT_ES6=1",
        "-sEXPORT_NAME=createAtomicStockfish",
        f"-sPTHREAD_POOL_SIZE={PTHREAD_POOL_SIZE}",
        # Fail pthread_create instead of waiting for a worker that the blocked
        # main runtime thread could never start.
        "-sPTHREAD_POOL_SIZE_STRICT=2",
        f"-sINITIAL_MEMORY={INITIAL_MEMORY}",
        "-sALLOW_MEMORY_GROWTH=0",
        f"-sSTACK_SIZE={STACK_SIZE}",
        "-sFORCE_FILESYSTEM=1",
        "-sNO_EXIT_RUNTIME=1",
        # _main must be listed: an explicit export list otherwise drops main(),
        # and with it the whole engine.
        "-sEXPORTED_FUNCTIONS=['_main','_atomic_uci','_malloc','_free']",
        "-sEXPORTED_RUNTIME_METHODS=['FS','ccall']",
        "-sWASM_BIGINT=1",
        # No -sDETERMINISTIC: it replaces the clock with a call counter, which
        # breaks movetime and every clock-based time control.
    ]
    if debug:
        flags.extend(["-sASSERTIONS=2", "-sSTACK_OVERFLOW_CHECK=2"])
    else:
        flags.append("-sASSERTIONS=0")
    return flags


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def gzip_size(path: Path) -> int:
    # mtime=0 keeps the measurement independent of the build time.
    return len(gzip.compress(path.read_bytes(), compresslevel=9, mtime=0))


def brotli_size(path: Path) -> int | None:
    try:
        import brotli  # type: ignore[import-not-found]
    except ImportError:
        return None
    return len(brotli.compress(path.read_bytes(), quality=11))


def tool_version(command: list[str], environment: dict[str, str]) -> str:
    completed = subprocess.run(
        command, check=True, capture_output=True, text=True, env=environment
    )
    return completed.stdout.splitlines()[0].strip()


def emscripten_version(compiler: str, environment: dict[str, str]) -> str:
    line = tool_version([compiler, "--version"], environment)
    # "emcc (Emscripten gcc/clang-like replacement + linker ...) 4.0.10 (hash)"
    for word in line.replace("(", " ").replace(")", " ").split():
        if word.count(".") == 2 and all(part.isdigit() for part in word.split(".")):
            return word
    raise RuntimeError(f"cannot parse the Emscripten version from: {line}")


def render_worker(out_dir: Path) -> Path:
    template = (SCRIPT_DIR / "atomic-stockfish.worker.js").read_text(encoding="utf-8")
    values = {
        "@PTHREAD_POOL_SIZE@": str(PTHREAD_POOL_SIZE),
        "@MAX_HASH_MIB@": str(MAX_HASH_MIB),
        "@MAX_NETWORK_BYTES@": str(MAX_NETWORK_BYTES),
    }
    for key, value in values.items():
        if key not in template:
            raise RuntimeError(f"worker template lacks {key}")
        template = template.replace(key, value)
    output = out_dir / "atomic-stockfish.worker.js"
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(template)
    return output


def build(out_dir: Path, compiler: str, debug: bool, allow_other_emscripten: bool) -> dict:
    node_build = load_node_build()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACT_NAMES + ("manifest.json",):
        (out_dir / name).unlink(missing_ok=True)

    environment = os.environ.copy()
    source_date_epoch = node_build.normalized_source_date_epoch(environment)
    environment["SOURCE_DATE_EPOCH"] = source_date_epoch

    version = emscripten_version(compiler, environment)
    if version != REQUIRED_EMSCRIPTEN and not allow_other_emscripten:
        raise RuntimeError(
            f"Emscripten {REQUIRED_EMSCRIPTEN} is required, found {version} "
            "(pass --allow-other-emscripten to override)"
        )

    sources = browser_sources(node_build)
    uci_only = (REPO_ROOT / "tests" / "wasm-engine" / "uci_only.h").resolve()
    prefix_map = SOURCE_ROOT.resolve().as_posix()
    repo_map = REPO_ROOT.resolve().as_posix()
    output_js = out_dir / "atomic-stockfish.mjs"

    flags = compile_flags(debug)
    flags.extend(
        [
            "-include",
            str(uci_only),
            f"-ffile-prefix-map={prefix_map}=.",
            f"-fdebug-prefix-map={prefix_map}=.",
            f"-ffile-prefix-map={repo_map}=..",
        ]
    )
    flags.extend(node_build.version_defines(environment))
    flags.extend(link_flags(debug))

    command = [compiler, *flags, *(str(source) for source in sources), "-o", str(output_js)]
    print(f"Building the Atomic-Stockfish browser worker in {out_dir}", flush=True)
    subprocess.run(command, check=True, env=environment)

    if (out_dir / "atomic-stockfish.worker.mjs").exists():
        raise RuntimeError("unexpected separate Emscripten pthread worker script")
    render_worker(out_dir)

    artifacts = []
    for name in ARTIFACT_NAMES:
        path = out_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"expected artifact was not produced: {path}")
        artifacts.append(
            {
                "name": name,
                "bytes": path.stat().st_size,
                "gzipBytes": gzip_size(path),
                "brotliBytes": brotli_size(path),
                "sha256": sha256(path),
            }
        )

    commit = node_build.git_output("rev-parse", "HEAD")
    dirty = node_build.git_output(
        "status", "--porcelain", "--untracked-files=no", "--", "src", "tests"
    )
    manifest = {
        "schemaVersion": 1,
        "target": "browser-worker",
        "sourceCommit": commit,
        "sourceTreeDirty": bool(dirty) if dirty is not None else None,
        "sourceDateEpoch": int(source_date_epoch),
        "debug": debug,
        "emscriptenVersion": version,
        "requiredEmscriptenVersion": REQUIRED_EMSCRIPTEN,
        "compiler": tool_version([compiler, "--version"], environment),
        "clang": clang_version(compiler, environment),
        "compileFlags": compile_flags(debug)
        + ["-include", "tests/wasm-engine/uci_only.h"]
        + node_build.version_defines(environment),
        "linkFlags": link_flags(debug),
        "sources": [
            source.relative_to(REPO_ROOT.resolve()).as_posix() for source in sources
        ],
        "initialMemoryBytes": INITIAL_MEMORY,
        "maximumMemoryBytes": INITIAL_MEMORY,
        "memoryGrowth": False,
        "stackSizeBytes": STACK_SIZE,
        "pthreadPoolSize": PTHREAD_POOL_SIZE,
        "maxThreads": PTHREAD_POOL_SIZE,
        "maxHashMiB": MAX_HASH_MIB,
        "maxNetworkBytes": MAX_NETWORK_BYTES,
        "entrypoints": {
            "worker": "atomic-stockfish.worker.js",
            "workerType": "module",
            "moduleFactory": "atomic-stockfish.mjs#createAtomicStockfish",
            "nativeExports": ["atomic_uci"],
        },
        "requiredHeaders": {
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Embedder-Policy": "require-corp",
        },
        "externalNetwork": True,
        "embeddedNetwork": False,
        "supportedNetworkBackends": [
            "Legacy Atomic V1",
            "AtomicNNUEV2",
            "AtomicNNUEV3",
        ],
        "networkFileVersions": ["0x7AF32F20", "0xA70C0002", "0xA70C0003"],
        "referenceNetwork": {
            "name": "atomic_run3b_e202_l05.nnue",
            "bytes": 47721376,
            "sha256": "99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6",
        },
        "artifacts": artifacts,
    }
    with (out_dir / "manifest.json").open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

    for artifact in artifacts:
        print(
            f"  {artifact['name']}  {artifact['bytes']} bytes "
            f"(gzip {artifact['gzipBytes']})  sha256={artifact['sha256']}"
        )
    return manifest


def clang_version(compiler: str, environment: dict[str, str]) -> str | None:
    completed = subprocess.run(
        [compiler, "-v"], check=False, capture_output=True, text=True, env=environment
    )
    for line in (completed.stderr + completed.stdout).splitlines():
        if "clang version" in line:
            return line.strip()
    return None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "build" / "wasm-browser")
    parser.add_argument("--compiler", default=shutil.which("em++") or "em++")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--allow-other-emscripten", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    build(args.out_dir, args.compiler, args.debug, args.allow_other_emscripten)
    return 0


if __name__ == "__main__":
    sys.exit(main())
