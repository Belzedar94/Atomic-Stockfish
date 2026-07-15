"""Focused units for the local-only AtomicNNUEV3 SIMD benchmark audit."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tests" / "atomic_v3_simd_benchmark.py"
SPEC = importlib.util.spec_from_file_location("atomic_v3_simd_benchmark", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
BENCH = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BENCH
SPEC.loader.exec_module(BENCH)


def _transcript(
    *,
    isa: str = "avx2",
    maximum: str = "avx2",
    promotion_gate: bool = True,
    scalar_ns: str = "380,390,400,410,420",
    required_ns: str = "90,95,100,105,110",
    scalar_median: str = "400",
    required_median: str = "100",
    ratio: str = "4.000000",
) -> str:
    gate = int(promotion_gate)
    return "\n".join(
        (
            "record=simd_identity",
            "network.version=0xA70C0003",
            "network.hash=0xCF9A484",
            "network.description=Atomic-Stockfish AtomicNNUEV3 controlled mixed-wire CI source",
            "wire.policy=identity",
            "wire.simd_permuted=1",
            f"isa.requested={isa}",
            f"isa.maximum={maximum}",
            "end_identity=1",
            "record=simd_benchmark",
            f"isa.requested={isa}",
            f"isa.executed={isa}",
            "warmups=1",
            "trials=5",
            "repetitions=8192",
            f"promotion_gate={gate}",
            f"scalar_ns={scalar_ns}",
            f"required_ns={required_ns}",
            f"scalar_median_ns={scalar_median}",
            f"required_median_ns={required_median}",
            f"throughput_ratio={ratio}",
            "sink=0x0123456789ABCDEF",
            "end_benchmark=1",
            "AtomicNNUEV3 SIMD benchmark passed: "
            f"requested={isa} executed={isa} warmups=1 trials=5 "
            f"ratio={ratio} promotion_gate={gate}",
            "",
        )
    )


def test_parse_promotion_transcript_is_exact():
    parsed = BENCH.parse_benchmark_output(
        _transcript(), required_isa="avx2", promotion_gate=True
    )
    assert parsed.scalar_ns == (380, 390, 400, 410, 420)
    assert parsed.required_ns == (90, 95, 100, 105, 110)
    assert parsed.scalar_median_ns == 400
    assert parsed.required_median_ns == 100
    assert str(parsed.throughput_ratio) == "4.000000"


def test_report_only_mode_does_not_enforce_a_noisy_speed_threshold():
    parsed = BENCH.parse_benchmark_output(
        _transcript(
            promotion_gate=False,
            scalar_ns="90,95,100,105,110",
            required_ns="180,190,200,210,220",
            scalar_median="100",
            required_median="200",
            ratio="0.500000",
        ),
        required_isa="avx2",
        promotion_gate=False,
    )
    assert str(parsed.throughput_ratio) == "0.500000"


@pytest.mark.parametrize(
    ("old", "new", "message"),
    (
        ("network.version=0xA70C0003", "network.version=0xA70C0002", "version"),
        ("network.hash=0xCF9A484", "network.hash=0x0000000", "network hash"),
        (
            "network.description=Atomic-Stockfish AtomicNNUEV3 controlled mixed-wire CI source",
            "network.description=untrusted fixture",
            "network description",
        ),
        ("trials=5", "trials=4", "trials differs"),
        ("repetitions=8192", "repetitions=4096", "repetitions differs"),
        ("isa.executed=avx2", "isa.executed=sse41", "executed differs"),
        ("wire.simd_permuted=1", "wire.simd_permuted=0", "not marked SIMD-permuted"),
    ),
)
def test_identity_and_contract_drift_fail_closed(old: str, new: str, message: str):
    with pytest.raises(BENCH.BenchmarkFailure, match=message):
        BENCH.parse_benchmark_output(
            _transcript().replace(old, new, 1),
            required_isa="avx2",
            promotion_gate=True,
        )


def test_output_after_sentinel_fails_closed():
    with pytest.raises(BENCH.BenchmarkFailure, match="after its success sentinel"):
        BENCH.parse_benchmark_output(
            _transcript() + "unexpected=1\n",
            required_isa="avx2",
            promotion_gate=True,
        )


def test_identity_wire_policy_still_requires_loaded_simd_permuted_invariant():
    parsed = BENCH.parse_benchmark_output(
        _transcript(), required_isa="avx2", promotion_gate=True
    )
    assert parsed.identity["wire.policy"] == "identity"
    assert parsed.identity["wire.simd_permuted"] == "1"


def test_sample_count_and_noncanonical_samples_fail_closed():
    for samples in ("90,95,100,105", "90,95,0,105,110", "090,95,100,105,110"):
        with pytest.raises(BENCH.BenchmarkFailure):
            BENCH.parse_benchmark_output(
                _transcript(required_ns=samples),
                required_isa="avx2",
                promotion_gate=True,
            )


def test_raw_samples_authenticate_both_reported_medians():
    for keyword in (
        {"scalar_median": "401"},
        {"required_median": "101"},
    ):
        with pytest.raises(BENCH.BenchmarkFailure, match="median"):
            BENCH.parse_benchmark_output(
                _transcript(**keyword),
                required_isa="avx2",
                promotion_gate=True,
            )


def test_ratio_is_recomputed_and_must_match_sentinel():
    with pytest.raises(BENCH.BenchmarkFailure, match="two medians"):
        BENCH.parse_benchmark_output(
            _transcript(ratio="3.999000"),
            required_isa="avx2",
            promotion_gate=True,
        )

    transcript = _transcript().replace(
        "ratio=4.000000 promotion_gate=1",
        "ratio=4.000001 promotion_gate=1",
    )
    with pytest.raises(BENCH.BenchmarkFailure, match="sentinel disagrees"):
        BENCH.parse_benchmark_output(
            transcript, required_isa="avx2", promotion_gate=True
        )


def test_compiled_maximum_must_satisfy_exact_request():
    with pytest.raises(BENCH.BenchmarkFailure, match="maximum ISA"):
        BENCH.parse_benchmark_output(
            _transcript(maximum="sse41"),
            required_isa="avx2",
            promotion_gate=True,
        )


def test_promotion_gate_rejects_scalar_and_nonpositive_speedup():
    with pytest.raises(BENCH.BenchmarkFailure, match="cannot target scalar"):
        BENCH.parse_benchmark_output(
            _transcript(isa="scalar", maximum="scalar"),
            required_isa="scalar",
            promotion_gate=True,
        )

    with pytest.raises(BENCH.BenchmarkFailure, match="did not beat forced scalar"):
        BENCH.parse_benchmark_output(
            _transcript(
                scalar_ns="90,95,100,105,110",
                required_ns="90,95,100,105,110",
                scalar_median="100",
                required_median="100",
                ratio="1.000000",
            ),
            required_isa="avx2",
            promotion_gate=True,
        )


def test_run_authenticates_before_and_after_and_forwards_opt_in_gate(
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
        runner,
        net,
        "avx2",
        promotion_gate=True,
        timeout=12.5,
    )
    assert authenticated == [net, net]
    assert commands[0][0][-2:] == ["--benchmark", "--promotion-gate"]
    assert "shell" not in commands[0][1]
    assert commands[0][1]["timeout"] == 12.5
    assert digest == "a" * 64
    assert transcript == _transcript()
    assert str(record.throughput_ratio) == "4.000000"


def test_success_with_stderr_and_nonzero_exit_both_fail_closed(
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
        BENCH.run_benchmark(
            runner, net, "avx2", promotion_gate=False, timeout=1.0
        )

    monkeypatch.setattr(
        BENCH.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 3, "", "failed"),
    )
    with pytest.raises(BENCH.BenchmarkFailure, match="exited 3"):
        BENCH.run_benchmark(
            runner, net, "avx2", promotion_gate=False, timeout=1.0
        )
