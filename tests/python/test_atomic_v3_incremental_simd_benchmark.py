"""Focused units for the representative incremental SIMD benchmark audit."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tests" / "atomic_v3_incremental_simd_benchmark.py"
SPEC = importlib.util.spec_from_file_location(
    "atomic_v3_incremental_simd_benchmark", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
BENCH = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BENCH
SPEC.loader.exec_module(BENCH)


def _transcript(
    *,
    isa: str = "avx2",
    maximum: str = "avx2",
    exactness: str = "1",
    ratio: str = "0.500000",
) -> str:
    lines = [
        "record=incremental_simd_benchmark_identity",
        "network.version=0xA70C0003",
        "network.hash=0xCF9A484",
        "network.description=Atomic-Stockfish AtomicNNUEV3 controlled mixed-wire CI source",
        "wire.policy=identity",
        "wire.simd_permuted=1",
        f"isa.requested={isa}",
        f"isa.maximum={maximum}",
        "end_identity=1",
    ]
    scalar_columns = []
    required_columns = []
    blasts = {"quiet": 0, "capture": 2, "promotion": 0, "en-passant": 3, "max-blast": 9}
    for index, (name, fen, move, _) in enumerate(BENCH.EXPECTED_CASES):
        scalar_median = 100 + index * 20
        required_median = scalar_median * 2
        scalar = tuple(scalar_median + offset for offset in (-20, -10, 0, 10, 20))
        required = tuple(required_median + offset * 2 for offset in (-20, -10, 0, 10, 20))
        scalar_columns.append(scalar)
        required_columns.append(required)
        lines.extend(
            (
                "record=transition_case",
                f"case={name}",
                f"fen={fen}",
                f"move={move}",
                f"blast_size={blasts[name]}",
                "removed_rows=4",
                "added_rows=2",
                f"scalar_ns={','.join(map(str, scalar))}",
                f"required_ns={','.join(map(str, required))}",
                f"scalar_median_ns={scalar_median}",
                f"required_median_ns={required_median}",
                f"speed_ratio={ratio}",
                f"exactness={exactness}",
                f"fingerprint=0x{index + 1:016X}",
                f"end_case={name}",
            )
        )
    scalar_totals = tuple(
        sum(column[trial] for column in scalar_columns) for trial in range(5)
    )
    required_totals = tuple(
        sum(column[trial] for column in required_columns) for trial in range(5)
    )
    scalar_median = sorted(scalar_totals)[2]
    required_median = sorted(required_totals)[2]
    lines.extend(
        (
            "record=incremental_simd_benchmark_summary",
            f"isa.requested={isa}",
            f"isa.executed={isa}",
            "cases=5",
            "warmups=1",
            "trials=5",
            "repetitions_per_case=128",
            "alternating_trials=1",
            f"exactness={exactness}",
            f"scalar_total_ns={','.join(map(str, scalar_totals))}",
            f"required_total_ns={','.join(map(str, required_totals))}",
            f"scalar_median_ns={scalar_median}",
            f"required_median_ns={required_median}",
            f"speed_ratio={ratio}",
            "sink=0x0123456789ABCDEF",
            "end_summary=1",
            "AtomicNNUEV3 incremental SIMD benchmark passed: "
            f"requested={isa} executed={isa} cases=5 warmups=1 trials=5 "
            f"ratio={ratio} exactness={exactness}",
        )
    )
    return "\n".join(lines) + "\n"


def test_complete_transcript_covers_each_transition_and_accepts_subunity_ratio():
    parsed = BENCH.parse_benchmark_output(_transcript(), required_isa="avx2")
    assert tuple(record.fields["case"] for record in parsed.cases) == (
        "quiet",
        "capture",
        "promotion",
        "en-passant",
        "max-blast",
    )
    assert str(parsed.speed_ratio) == "0.500000"
    assert all(str(record.speed_ratio) == "0.500000" for record in parsed.cases)


def test_exactness_is_mandatory_for_cases_and_summary():
    with pytest.raises(BENCH.BenchmarkFailure, match="exactness"):
        BENCH.parse_benchmark_output(
            _transcript(exactness="0"), required_isa="avx2"
        )


def test_case_order_fixture_and_blast_semantics_fail_closed():
    transcript = _transcript().replace("case=quiet", "case=capture", 1)
    with pytest.raises(BENCH.BenchmarkFailure, match="case order"):
        BENCH.parse_benchmark_output(transcript, required_isa="avx2")

    transcript = _transcript().replace("move=e2e4", "move=e2e3", 1)
    with pytest.raises(BENCH.BenchmarkFailure, match="fixture identity"):
        BENCH.parse_benchmark_output(transcript, required_isa="avx2")

    transcript = _transcript().replace("blast_size=9", "blast_size=8", 1)
    with pytest.raises(BENCH.BenchmarkFailure, match="blast size"):
        BENCH.parse_benchmark_output(transcript, required_isa="avx2")


def test_all_five_samples_and_reported_medians_are_recomputed():
    with pytest.raises(BENCH.BenchmarkFailure, match="contains 4 samples"):
        BENCH.parse_benchmark_output(
            _transcript().replace("scalar_ns=80,90,100,110,120", "scalar_ns=80,90,100,110", 1),
            required_isa="avx2",
        )

    with pytest.raises(BENCH.BenchmarkFailure, match="scalar median differs"):
        BENCH.parse_benchmark_output(
            _transcript().replace("scalar_median_ns=100", "scalar_median_ns=101", 1),
            required_isa="avx2",
        )


def test_summary_totals_are_exact_sums_of_per_case_samples():
    transcript = _transcript().replace(
        "scalar_total_ns=600,650,700,750,800",
        "scalar_total_ns=601,650,700,750,800",
        1,
    )
    with pytest.raises(BENCH.BenchmarkFailure, match="scalar totals"):
        BENCH.parse_benchmark_output(transcript, required_isa="avx2")


def test_ratio_and_success_sentinel_must_match_recomputed_summary():
    with pytest.raises(BENCH.BenchmarkFailure, match="two medians"):
        BENCH.parse_benchmark_output(
            _transcript(ratio="0.400000"), required_isa="avx2"
        )

    transcript = _transcript().replace(
        "ratio=0.500000 exactness=1\n",
        "ratio=0.500001 exactness=1\n",
        1,
    )
    with pytest.raises(BENCH.BenchmarkFailure, match="sentinel disagrees"):
        BENCH.parse_benchmark_output(transcript, required_isa="avx2")


def test_identity_and_compiled_maximum_are_fail_closed():
    with pytest.raises(BENCH.BenchmarkFailure, match="network hash"):
        BENCH.parse_benchmark_output(
            _transcript().replace("network.hash=0xCF9A484", "network.hash=0x0", 1),
            required_isa="avx2",
        )
    with pytest.raises(BENCH.BenchmarkFailure, match="maximum ISA"):
        BENCH.parse_benchmark_output(
            _transcript(maximum="sse41"), required_isa="avx2"
        )


def test_extra_output_and_noncanonical_samples_are_rejected():
    with pytest.raises(BENCH.BenchmarkFailure, match="after its success sentinel"):
        BENCH.parse_benchmark_output(
            _transcript() + "unexpected=1\n", required_isa="avx2"
        )
    with pytest.raises(BENCH.BenchmarkFailure, match="canonical integer"):
        BENCH.parse_benchmark_output(
            _transcript().replace(
                "scalar_ns=80,90,100,110,120",
                "scalar_ns=080,90,100,110,120",
                1,
            ),
            required_isa="avx2",
        )


def test_run_authenticates_before_and_after_and_requests_only_exact_isa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runner = tmp_path / "runner.exe"
    net = tmp_path / "fixture.nnue"
    runner.write_bytes(b"runner")
    net.write_bytes(b"fixture")
    authenticated = []
    commands = []

    def fake_authenticate(path: Path) -> str:
        authenticated.append(path)
        return "a" * 64

    def fake_run(command, **kwargs):
        commands.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, _transcript(), "")

    monkeypatch.setattr(BENCH.differential, "authenticate_fixture", fake_authenticate)
    monkeypatch.setattr(BENCH.subprocess, "run", fake_run)
    record, digest, transcript = BENCH.run_benchmark(
        runner, net, "avx2", timeout=12.5
    )
    assert authenticated == [net, net]
    assert commands[0][0][-2:] == ["--require-isa", "avx2"]
    assert "shell" not in commands[0][1]
    assert commands[0][1]["timeout"] == 12.5
    assert digest == "a" * 64
    assert transcript == _transcript()
    assert str(record.speed_ratio) == "0.500000"


def test_success_with_stderr_and_nonzero_exit_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runner = tmp_path / "runner.exe"
    net = tmp_path / "fixture.nnue"
    runner.write_bytes(b"runner")
    net.write_bytes(b"fixture")
    monkeypatch.setattr(BENCH.differential, "authenticate_fixture", lambda path: "a" * 64)
    monkeypatch.setattr(
        BENCH.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, _transcript(), "warning\n"
        ),
    )
    with pytest.raises(BENCH.BenchmarkFailure, match="emitted stderr"):
        BENCH.run_benchmark(runner, net, "avx2", timeout=1.0)

    monkeypatch.setattr(
        BENCH.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 3, "", "failed"),
    )
    with pytest.raises(BENCH.BenchmarkFailure, match="exited 3"):
        BENCH.run_benchmark(runner, net, "avx2", timeout=1.0)


def test_cpp_contract_alternates_trials_and_has_no_speed_promotion_gate():
    source = (
        ROOT
        / "src"
        / "nnue"
        / "atomic_v3"
        / "tests"
        / "incremental_simd_benchmark.cpp"
    ).read_text(encoding="utf-8")
    assert "(trial & 1U)" in source
    assert "constexpr unsigned Warmups            = 1;" in source
    assert "constexpr unsigned Trials             = 5;" in source
    assert "promotionGate" not in source
    assert "ratio >" not in source
