from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "atomic.yml"


def workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def job(text: str, name: str, next_name: str) -> str:
    return text.split(f"  {name}:\n", 1)[1].split(f"  {next_name}:\n", 1)[0]


def test_gcc_debug_assert_job_is_explicit_and_executes_the_engine() -> None:
    section = job(workflow(), "debug", "debug-clang")
    assert "runs-on: ubuntu-24.04" in section
    assert (
        "make -j2 ARCH=x86-64 COMP=gcc COMPCXX=g++ "
        "debug=yes optimize=no build"
    ) in section
    assert "debug=yes optimize=no atomic-unit-tests" in section
    assert "debug=yes optimize=no atomic-api-tests" in section
    assert "tests/perft.sh src/atomic-stockfish" in section
    assert "tests/atomic_search.py --candidate src/atomic-stockfish" in section


def test_clang_debug_assert_job_builds_focused_units_and_smokes_protocols() -> None:
    section = job(workflow(), "debug-clang", "data-generator-windows")
    assert "runs-on: ubuntu-24.04" in section
    compiler = "ARCH=x86-64 COMP=clang COMPCXX=clang++ debug=yes optimize=no"
    assert f"{compiler} build" in section
    assert f"{compiler} atomic-unit-tests" in section
    assert f"{compiler} atomic-api-tests" in section
    assert f"{compiler} atomic-v2-backend-core-tests.bin" in section
    assert "src/atomic-v2-backend-core-tests.bin \"$RUNNER_TEMP/atomic-v2.nnue\"" in section
    assert "tests/release_protocol_smoke.py --engine src/atomic-stockfish" in section


def test_mingw_debug_assert_lane_cleans_release_objects_and_runs_smoke() -> None:
    windows = job(workflow(), "data-generator-windows", "sanitizers")
    assert "runs-on: windows-2022" in windows
    assert "shell: msys2 {0}" in windows
    assert "install: mingw-w64-x86_64-gcc make" in windows
    section = windows.split("- name: MinGW debug/assert build and protocol smoke", 1)[1]
    clean = "make -C src ARCH=x86-64 COMP=mingw clean"
    compiler = "ARCH=x86-64 COMP=mingw debug=yes optimize=no"
    build = f"make -C src -j2 {compiler} build"
    assert clean in section
    assert build in section
    assert section.index(clean) < section.index(build)
    assert f"make -C src -j2 {compiler} atomic-unit-tests" in section
    assert f"make -C src -j2 {compiler} atomic-api-tests" in section
    assert "tests/release_protocol_smoke.py --engine src/atomic-stockfish.exe" in section


def test_debug_contract_is_part_of_the_normal_python_ci_gate() -> None:
    assert "tests/python/test_atomic_debug_ci_contract.py" in workflow()
