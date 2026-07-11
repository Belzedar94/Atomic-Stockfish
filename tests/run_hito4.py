#!/usr/bin/env python3
"""Fail-fast Hito 4 validation for native, Python, JavaScript and WASM surfaces.

Release mode is the default and requires the Node UCI/NNUE WASM launcher.  The
explicit ``--allow-missing-wasm`` switch exists only so an in-progress local
build can validate every already-built surface while the release WASM artifact
is still being produced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
EXPECTED_NET_SHA256 = (
    "99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6"
)
EXPECTED_SIGNATURE = "356852"


class GateFailure(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command_path(value: str, label: str) -> str:
    resolved = shutil.which(value)
    if resolved:
        return resolved
    candidate = Path(value).expanduser().resolve()
    if candidate.is_file():
        return str(candidate)
    raise GateFailure(f"{label} executable was not found: {value}")


def default_bash() -> str:
    if os.name == "nt":
        git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/bin/bash.exe"
        if git_bash.is_file():
            return str(git_bash)
    return "bash"


def require_path(path: Path, label: str, *, directory: bool = False) -> Path:
    resolved = path.expanduser().resolve()
    valid = resolved.is_dir() if directory else resolved.is_file()
    if not valid:
        kind = "directory" if directory else "file"
        raise GateFailure(f"{label} {kind} does not exist: {resolved}")
    return resolved


def validate_wasm_manifest(wrapper: Path) -> Path:
    manifest_path = require_path(wrapper.parent / "manifest.json", "WASM artifact manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GateFailure(f"invalid WASM artifact manifest {manifest_path}: {error}") from error

    if manifest.get("target") != "node-uci-nnue":
        raise GateFailure("WASM manifest target must be node-uci-nnue")
    if manifest.get("supportedEntrypoint") != wrapper.name:
        raise GateFailure(
            "--wasm-wrapper is not the supported entrypoint recorded in the manifest"
        )
    if manifest.get("directRuntimeGlueSupported") is not False:
        raise GateFailure("WASM manifest must reject direct generated-glue execution")

    expected_names = {
        "atomic-stockfish-nnue.js",
        "atomic-stockfish-nnue.wasm",
        "atomic-stockfish-nnue.worker.js",
        wrapper.name,
    }
    rows = manifest.get("artifacts")
    if (
        not isinstance(rows, list)
        or not all(isinstance(row, dict) for row in rows)
        or {row.get("name") for row in rows} != expected_names
    ):
        raise GateFailure("WASM manifest does not list the exact four release artifacts")
    for row in rows:
        artifact = require_path(wrapper.parent / row["name"], f"WASM artifact {row['name']}")
        if artifact.stat().st_size != row.get("bytes"):
            raise GateFailure(f"WASM artifact size differs from manifest: {artifact}")
        if sha256(artifact) != row.get("sha256"):
            raise GateFailure(f"WASM artifact SHA-256 differs from manifest: {artifact}")
    return manifest_path


def run_step(
    label: str,
    command: Sequence[str],
    *,
    cwd: Path = REPO_ROOT,
    env: Mapping[str, str] | None = None,
    timeout: float,
    required_markers: Iterable[str] = (),
    expected_pass_lines: int | None = None,
) -> str:
    print(f"\n=== {label} ===", flush=True)
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GateFailure(f"{label} could not complete: {error}") from error

    output = completed.stdout or ""
    if output:
        print(output, end="" if output.endswith("\n") else "\n", flush=True)
    if completed.returncode:
        raise GateFailure(f"{label} exited with code {completed.returncode}")

    for marker in required_markers:
        if marker not in output:
            raise GateFailure(f"{label} did not emit required marker: {marker!r}")
    if expected_pass_lines is not None:
        pass_lines = sum(line.startswith("PASS ") for line in output.splitlines())
        if pass_lines != expected_pass_lines:
            raise GateFailure(
                f"{label} emitted {pass_lines} PASS lines; expected {expected_pass_lines}"
            )
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--net", type=Path, required=True)
    parser.add_argument(
        "--pyffish",
        type=Path,
        required=True,
        help="exact pyffish extension file, or directory containing it",
    )
    parser.add_argument("--cjs", type=Path, required=True, help="CommonJS glue artifact")
    parser.add_argument("--esm", type=Path, required=True, help="ES module glue artifact")
    parser.add_argument("--tables", type=Path, required=True)
    parser.add_argument(
        "--wasm-wrapper",
        type=Path,
        help="Node UCI/NNUE WASM launcher; mandatory unless --allow-missing-wasm is explicit",
    )
    parser.add_argument(
        "--allow-missing-wasm",
        action="store_true",
        help="development-only: omit the unfinished WASM UCI/NNUE release gate",
    )
    parser.add_argument("--cpp-unit", type=Path)
    parser.add_argument("--cpp-api", type=Path)
    parser.add_argument(
        "--syzygy-driver",
        type=Path,
        default=WORKSPACE_ROOT / "research" / "atomic-syzygy-driver.exe",
    )
    parser.add_argument("--fairy-repo", type=Path, default=WORKSPACE_ROOT / "Fairy-Stockfish")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--node", default="node")
    parser.add_argument("--bash", default=default_bash())
    parser.add_argument("--timeout", type=float, default=300.0)
    return parser.parse_args()


def validate_inputs(args: argparse.Namespace) -> dict[str, object]:
    if args.timeout <= 0:
        raise GateFailure("--timeout must be positive")
    if args.wasm_wrapper is None and not args.allow_missing_wasm:
        raise GateFailure(
            "release mode requires --wasm-wrapper; use --allow-missing-wasm only for an "
            "explicit development run"
        )

    native = require_path(args.native, "native engine")
    suffix = ".exe" if native.suffix.lower() == ".exe" else ".bin"
    cpp_unit = require_path(
        args.cpp_unit or native.with_name(f"atomic-unit-tests{suffix}"), "C++ rule unit test"
    )
    cpp_api = require_path(
        args.cpp_api or native.with_name(f"atomic-api-tests{suffix}"), "C++ API unit test"
    )
    net = require_path(args.net, "Legacy Atomic V1 network")
    actual_net_sha = sha256(net)
    if actual_net_sha != EXPECTED_NET_SHA256:
        raise GateFailure(
            f"network SHA-256 is {actual_net_sha}; expected {EXPECTED_NET_SHA256}"
        )

    pyffish = args.pyffish.expanduser().resolve()
    if not (pyffish.is_file() or pyffish.is_dir()):
        raise GateFailure(f"pyffish artifact does not exist: {pyffish}")

    result: dict[str, object] = {
        "native": native,
        "net": net,
        "pyffish": pyffish,
        "pyffish_root": pyffish if pyffish.is_dir() else pyffish.parent,
        "cjs": require_path(args.cjs, "CommonJS artifact"),
        "esm": require_path(args.esm, "ES module artifact"),
        "tables": require_path(args.tables, "Atomic Syzygy tables", directory=True),
        "cpp_unit": cpp_unit,
        "cpp_api": cpp_api,
        "syzygy_driver": require_path(args.syzygy_driver, "Atomic Syzygy driver"),
        "fairy_repo": require_path(args.fairy_repo, "frozen Fairy repository", directory=True),
        "python": command_path(args.python, "Python"),
        "node": command_path(args.node, "Node"),
        "bash": command_path(args.bash, "Bash"),
    }
    if args.wasm_wrapper is not None:
        wrapper = require_path(args.wasm_wrapper, "WASM UCI/NNUE wrapper")
        result["wasm_wrapper"] = wrapper
        result["wasm_manifest"] = validate_wasm_manifest(wrapper)
    return result


def main() -> int:
    args = parse_args()
    try:
        paths = validate_inputs(args)
        native = paths["native"]
        net = paths["net"]
        pyffish = paths["pyffish"]
        cjs = paths["cjs"]
        esm = paths["esm"]
        tables = paths["tables"]
        python = str(paths["python"])
        node = str(paths["node"])
        bash = str(paths["bash"])

        base_env = os.environ.copy()
        # A directly selected MSYS bash does not automatically put dirname,
        # sed, or a nested `bash` invocation on PATH when launched from a
        # regular Windows Python process. Keep every shell gate on the same
        # toolchain as the explicitly validated executable.
        bash_dir = str(Path(bash).resolve().parent)
        base_env["PATH"] = bash_dir + os.pathsep + base_env.get("PATH", "")
        python_env = base_env.copy()
        python_env["PYTHONPATH"] = str(paths["pyffish_root"])
        python_env["PYTHONSAFEPATH"] = "1"

        run_step(
            "C++ Atomic rule/state units",
            [str(paths["cpp_unit"])],
            timeout=args.timeout,
            required_markers=("Atomic C++ unit tests passed: 30/30",),
            expected_pass_lines=30,
        )
        run_step(
            "C++ shared Atomic API units",
            [str(paths["cpp_api"])],
            timeout=args.timeout,
            required_markers=("Atomic API unit tests passed",),
            expected_pass_lines=29,
        )
        run_step(
            "frozen binding fixture inventory",
            [
                python,
                str(REPO_ROOT / "tests/bindings/validate_fixtures.py"),
                "--fairy-repo",
                str(paths["fairy_repo"]),
            ],
            timeout=args.timeout,
            required_markers=(
                "binding fixture validation passed: 58 fixtures, 22 Python tests, "
                "58 JavaScript tests, 8 perft vectors",
            ),
        )

        import_probe = (
            "import pathlib,pyffish,sys; "
            "actual=pathlib.Path(pyffish.__file__).resolve(); "
            "requested=pathlib.Path(sys.argv[1]).resolve(); "
            "ok=(actual==requested if requested.is_file() else "
            "(actual==requested or requested in actual.parents)); "
            "assert ok, f'pyffish resolved to {actual}, not {requested}'; "
            "print(f'pyffish artifact: {actual}')"
        )
        run_step(
            "explicit pyffish artifact resolution",
            [python, "-P", "-c", import_probe, str(pyffish)],
            env=python_env,
            timeout=args.timeout,
            required_markers=("pyffish artifact:",),
        )

        unittest_launcher = (
            "import runpy,sys; sys.path.insert(0,sys.argv[1]); target=sys.argv[2]; "
            "sys.argv=[target]; runpy.run_path(target,run_name='__main__')"
        )
        run_step(
            "historical test.py compatibility suite",
            [
                python,
                "-P",
                "-c",
                unittest_launcher,
                str(paths["pyffish_root"]),
                str(REPO_ROOT / "test.py"),
            ],
            env=python_env,
            timeout=args.timeout,
            required_markers=("Ran 22 tests", "OK"),
        )

        pytest_launcher = (
            "import pytest,sys; sys.path.insert(0,sys.argv[1]); "
            "raise SystemExit(pytest.main(sys.argv[2:]))"
        )
        run_step(
            "pytest binding and concurrent lifecycle suite",
            [
                python,
                "-P",
                "-c",
                pytest_launcher,
                str(paths["pyffish_root"]),
                "-q",
                "--maxfail=1",
                str(REPO_ROOT / "tests/python/test_pyffish.py"),
            ],
            env=python_env,
            timeout=args.timeout,
            required_markers=("54 passed",),
        )

        cjs_launcher = r"""
const path = require('node:path');
const createModule = require(path.resolve(process.argv[1]));
const { runSuite } = require(path.resolve(process.argv[2]));
(async () => {
  const artifact = path.resolve(process.argv[1]);
  const module = await createModule({
    locateFile(file) { return path.join(path.dirname(artifact), file); },
  });
  await runSuite(module, 'CommonJS/WASM');
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        run_step(
            "CommonJS Board WASM suite",
            [node, "-e", cjs_launcher, str(cjs), str(REPO_ROOT / "tests/js/test-suite.cjs")],
            timeout=args.timeout,
            required_markers=("CommonJS/WASM: 58 fixtures and binding lifecycle checks passed",),
        )

        esm_launcher = r"""
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const artifact = path.resolve(process.argv[1]);
const createModule = (await import(pathToFileURL(artifact).href)).default;
const suite = (await import(pathToFileURL(path.resolve(process.argv[2])).href)).default;
const module = await createModule({
  locateFile(file) { return path.join(path.dirname(artifact), file); },
});
await suite.runSuite(module, 'ES module/WASM');
"""
        run_step(
            "ES module Board WASM suite",
            [
                node,
                "--input-type=module",
                "-e",
                esm_launcher,
                str(esm),
                str(REPO_ROOT / "tests/js/test-suite.cjs"),
            ],
            timeout=args.timeout,
            required_markers=(
                "ES module/WASM: 58 fixtures and binding lifecycle checks passed",
            ),
        )

        run_step(
            "exact native/Python/CommonJS/ESM parity",
            [
                python,
                str(REPO_ROOT / "tests/bindings/cross_surface.py"),
                "--native",
                str(native),
                "--pyffish",
                str(pyffish),
                "--cjs",
                str(cjs),
                "--esm",
                str(esm),
                "--node",
                node,
            ],
            timeout=args.timeout,
            required_markers=(
                "PASS Exact cross-surface parity 40 binding fixtures, 25 native intersections",
            ),
        )

        run_step(
            "Atomic and Atomic960 perft/rule transitions",
            [bash, str(REPO_ROOT / "tests/perft.sh"), str(native)],
            env=base_env,
            timeout=args.timeout,
            required_markers=("Atomic perft and rule-transition suite passed",),
        )
        run_step(
            "Atomic terminal-search regressions with NNUE",
            [
                python,
                str(REPO_ROOT / "tests/atomic_search.py"),
                "--candidate",
                str(native),
                "--eval-file",
                str(net),
                "--use-nnue",
                "true",
            ],
            timeout=args.timeout,
            required_markers=("Atomic search regressions passed: 7/7",),
        )
        run_step(
            "XBoard/CECP Atomic, analyze and live ponder",
            [python, str(REPO_ROOT / "tests/xboard_protocol.py"), "--candidate", str(native)],
            timeout=args.timeout,
            required_markers=("XBoard Atomic protocol passed",),
        )
        run_step(
            "Atomic Syzygy format/domain/real-table probes",
            [
                python,
                str(REPO_ROOT / "tests/atomic_syzygy.py"),
                "--driver",
                str(paths["syzygy_driver"]),
                "--tables",
                str(tables),
            ],
            timeout=args.timeout,
            required_markers=(
                "PASS real table fixtures: 11 headers and SHA-256 hashes",
                "Atomic Syzygy tests passed: 5/5",
            ),
        )
        run_step(
            "Atomic Syzygy production UCI wiring",
            [
                python,
                str(REPO_ROOT / "tests/atomic_syzygy_uci.py"),
                "--engine",
                str(native),
                "--tables",
                str(tables),
                "--eval-file",
                str(net),
            ],
            timeout=args.timeout,
            required_markers=("Atomic Syzygy UCI tests passed:",),
        )
        run_step(
            "Legacy Atomic V1 NNUE modes and byte-exact export",
            [
                python,
                str(REPO_ROOT / "tests/nnue_modes.py"),
                "--engine",
                str(native),
                "--eval-file",
                str(net),
            ],
            timeout=args.timeout,
            required_markers=("NNUE mode contract passed:",),
        )
        run_step(
            "deterministic Atomic reprosearch",
            [
                python,
                str(REPO_ROOT / "tests/reprosearch.py"),
                "--engine",
                str(native),
                "--eval-file",
                str(net),
            ],
            timeout=args.timeout,
            required_markers=("Atomic reprosearch passed: 12/12",),
        )

        signature_env = base_env.copy()
        signature_env["EXE"] = str(native)
        signature_env["ATOMIC_NNUE_NET"] = str(net)
        run_step(
            "Atomic NNUE search signature",
            [bash, str(REPO_ROOT / "tests/signature.sh"), EXPECTED_SIGNATURE],
            env=signature_env,
            timeout=args.timeout,
            required_markers=(f"signature OK: {EXPECTED_SIGNATURE}",),
        )
        run_step(
            "native UCI/XBoard runtime smoke",
            [
                python,
                str(REPO_ROOT / "tests/instrumented.py"),
                "--none",
                str(native),
                "--eval-file",
                str(net),
            ],
            timeout=args.timeout,
            required_markers=("Atomic instrumented runtime passed:",),
        )

        if "wasm_wrapper" in paths:
            run_step(
                "Node UCI/NNUE WASM integration",
                [
                    node,
                    str(REPO_ROOT / "tests/wasm-engine/run-engine-tests.mjs"),
                    "--engine",
                    str(paths["wasm_wrapper"]),
                    "--net",
                    str(net),
                ],
                timeout=max(args.timeout, 600.0),
                required_markers=(
                    f"WASM engine integration: PASS (net sha256={EXPECTED_NET_SHA256})",
                ),
            )
        else:
            print(
                "\nDEVELOPMENT MODE: WASM UCI/NNUE release gate was explicitly omitted; "
                "this result is not a Hito 4 release pass.",
                flush=True,
            )

        print(
            "\nHito 4 validation passed"
            + (" (development run without WASM UCI/NNUE)" if "wasm_wrapper" not in paths else ""),
            flush=True,
        )
        return 0
    except GateFailure as error:
        print(f"\nHito 4 validation FAILED: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
