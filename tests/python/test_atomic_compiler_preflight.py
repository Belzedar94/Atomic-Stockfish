from __future__ import annotations

import ast
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import atomic_bench_compare as bench_compare
import atomic_bench_ab as bench_ab
import atomic_compiler_preflight as compiler_preflight


BMI2_SIGNATURE = compiler_preflight.CompilationSignature(
    bitness="64bit",
    isa=frozenset({"BMI2", "AVX2", "SSE41", "SSSE3", "SSE2", "POPCNT"}),
    compiler_family="g++ (GNUC)",
    compiler_version="15.2.0",
    version_macro="15.2.0",
)

ATOMIC_COMPILER_OUTPUT = """
Compiled by                : g++ (GNUC) 15.2.0 on MinGW64
Compilation architecture   : x86-64-bmi2
Compilation settings       : 64bit BMI2 AVX2 SSE41 SSSE3 SSE2 POPCNT
Compiler __VERSION__ macro : 15.2.0
"""

FAIRY_COMPILER_OUTPUT = """
Compiled by g++ (GNUC) 15.2.0 on MinGW64
Compilation settings include:  64bit BMI2 AVX2 SSE41 SSSE3 SSE2 POPCNT
__VERSION__ macro expands to: 15.2.0
"""


@pytest.mark.parametrize("output", [ATOMIC_COMPILER_OUTPUT, FAIRY_COMPILER_OUTPUT])
def test_parser_accepts_real_atomic_and_fairy_formats(output):
    signature = compiler_preflight.parse_compilation_settings(output)
    assert signature == BMI2_SIGNATURE
    assert signature.display() == "64bit BMI2 AVX2 SSE41 SSSE3 SSE2 POPCNT"
    assert signature.compiler_display() == "g++ (GNUC) 15.2.0"


def test_parser_preserves_debug_build_mode_and_normalizes_target_order():
    output = (
        "Compiled by: g++ (GNUC) 15.2.0 on MinGW64\n"
        "compilation SETTINGS INCLUDE : "
        "popcnt sse2 ssse3 sse41 avx2 bmi2 64BIT debug\n"
        "Compiler __VERSION__ macro: 15.2.0\n"
    )
    signature = compiler_preflight.parse_compilation_settings(output)
    assert signature.debug
    assert signature.display() == "64bit BMI2 AVX2 SSE41 SSSE3 SSE2 POPCNT DEBUG"


@pytest.mark.parametrize(
    ("output", "message"),
    [
        (
            "Compiled by: g++ (GNUC) 15.2.0 on MinGW64\n"
            "Compiler __VERSION__ macro: 15.2.0\n",
            "no Compilation settings",
        ),
        (
            "Compilation settings: 64bit BMI2 AVX2 SSE41 SSSE3 SSE2 POPCNT\n"
            "Compiler __VERSION__ macro: 15.2.0\n",
            "no Compiled by",
        ),
        (
            "Compiled by: g++ (GNUC) 15.2.0 on MinGW64\n"
            "Compilation settings: 64bit BMI2 AVX2 SSE41 SSSE3 SSE2 POPCNT\n",
            "no __VERSION__ macro",
        ),
        (
            "Compiled by: g++ (GNUC) 15.2.0 on MinGW64\n"
            "Compilation settings: 64bit SSE2\n"
            "Compilation settings include: 64bit SSE2\n"
            "Compiler __VERSION__ macro: 15.2.0\n",
            "2 Compilation settings lines",
        ),
        (
            "Compiled by: g++ (GNUC) 15.2.0 on MinGW64\n"
            "Compilation settings: AVX2 SSE2\n"
            "Compiler __VERSION__ macro: 15.2.0\n",
            "exactly one",
        ),
        (
            "Compiled by: g++ (GNUC) 15.2.0 on MinGW64\n"
            "Compilation settings: 64bit SSE2 SSE2\n"
            "Compiler __VERSION__ macro: 15.2.0\n",
            "duplicate",
        ),
        (
            "Compiled by: g++ (GNUC) 15.2.0 on MinGW64\n"
            "Compilation settings: 64bit SSE2 FUTURE_SIMD\n"
            "Compiler __VERSION__ macro: 15.2.0\n",
            "unknown tokens",
        ),
        (
            "Compiled by: g++ (GNUC) 15.2.0 on MinGW64\n"
            "Compilation settings: 64bit BMI2 AVX2 SSE41 SSSE3 SSE2 POPCNT\n"
            "Compiler __VERSION__ macro: 14.2.0\n",
            "compiler version mismatch",
        ),
    ],
)
def test_parser_rejects_missing_ambiguous_or_inconsistent_output(output, message):
    with pytest.raises(compiler_preflight.CompilerPreflightError, match=message):
        compiler_preflight.parse_compilation_settings(output, label="candidate")


def test_matching_preflight_prints_target_compiler_and_preflight_hashes(
    tmp_path, monkeypatch, capsys
):
    calls = []

    def fake_probe(executable, *, label, timeout):
        calls.append((Path(executable), label, timeout))
        return BMI2_SIGNATURE

    monkeypatch.setattr(
        compiler_preflight, "probe_compilation_settings", fake_probe
    )
    candidate = tmp_path / "candidate.exe"
    baseline = tmp_path / "fairy.exe"
    candidate.write_bytes(b"candidate")
    baseline.write_bytes(b"baseline")
    signature = compiler_preflight.require_matching_compilation_settings(
        candidate, baseline, timeout=3.5
    )

    assert signature == BMI2_SIGNATURE
    assert calls == [
        (candidate, "candidate", 3.5),
        (baseline, "baseline", 3.5),
    ]
    output = capsys.readouterr().out.splitlines()
    assert output == [
        "Compiler preflight: PASS "
        "signature=64bit BMI2 AVX2 SSE41 SSSE3 SSE2 POPCNT "
        "compiler=g++ (GNUC) 15.2.0",
        "Engine artifacts preflight: "
        "candidate_bytes=9 "
        "candidate_sha256=DDA18A0E21AE47C53B4309434CBC02AE8BF764FA83A6DEFBB719431242722AA7 "
        "baseline_bytes=8 "
        "baseline_sha256=8BA8496A2525AE171FFD104D632DEDE6EF418D9B95962A9D88E2FCDBC8D48D24",
    ]


@pytest.mark.parametrize(
    ("candidate", "baseline", "message"),
    [
        (
            compiler_preflight.CompilationSignature(
                "64bit",
                frozenset({"AVX2", "SSE41", "SSSE3", "SSE2", "POPCNT"}),
                "g++ (GNUC)",
                "15.2.0",
                "15.2.0",
            ),
            BMI2_SIGNATURE,
            "candidate is not the exact normative BMI2 release target",
        ),
        (
            BMI2_SIGNATURE,
            compiler_preflight.CompilationSignature(
                BMI2_SIGNATURE.bitness,
                BMI2_SIGNATURE.isa,
                "clang++ (clang)",
                "15.2.0",
                "15.2.0",
            ),
            "compiler family mismatch",
        ),
        (
            BMI2_SIGNATURE,
            compiler_preflight.CompilationSignature(
                BMI2_SIGNATURE.bitness,
                BMI2_SIGNATURE.isa,
                BMI2_SIGNATURE.compiler_family,
                "14.2.0",
                "14.2.0",
            ),
            "compiler version mismatch",
        ),
    ],
)
def test_preflight_rejects_non_normative_target_or_compiler_identity(
    tmp_path, monkeypatch, candidate, baseline, message
):
    candidate_path = tmp_path / "candidate.exe"
    baseline_path = tmp_path / "baseline.exe"
    candidate_path.touch()
    baseline_path.touch()
    signatures = iter((candidate, baseline))
    monkeypatch.setattr(
        compiler_preflight,
        "probe_compilation_settings",
        lambda *args, **kwargs: next(signatures),
    )

    with pytest.raises(compiler_preflight.CompilerPreflightError, match=message):
        compiler_preflight.require_matching_compilation_settings(
            candidate_path, baseline_path
        )


def test_preflight_enforces_exact_frozen_baseline_sha_before_probe(
    tmp_path, monkeypatch
):
    candidate = tmp_path / "candidate.exe"
    baseline = tmp_path / "baseline.exe"
    candidate.write_bytes(b"candidate")
    baseline.write_bytes(b"wrong baseline")
    called = False

    def fake_probe(*args, **kwargs):
        nonlocal called
        called = True
        return BMI2_SIGNATURE

    monkeypatch.setattr(
        compiler_preflight, "probe_compilation_settings", fake_probe
    )
    with pytest.raises(
        compiler_preflight.CompilerPreflightError, match="frozen Fairy-Stockfish"
    ):
        compiler_preflight.require_matching_compilation_settings(
            candidate,
            baseline,
            expected_baseline_sha256=compiler_preflight.NORMATIVE_BASELINE_SHA256,
        )
    assert not called


def test_file_postflight_detects_mutation_and_does_not_emit_pass(tmp_path, capsys):
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"before")
    before = compiler_preflight.fingerprint_files((("artifact", artifact),))
    artifact.write_bytes(b"after")

    with pytest.raises(compiler_preflight.CompilerPreflightError, match="changed"):
        compiler_preflight.verify_file_fingerprints(
            before, emit=print, pass_label="Workload postflight"
        )
    assert "PASS" not in capsys.readouterr().out


def test_file_postflight_emits_explicit_pass_for_unchanged_files(tmp_path, capsys):
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"stable")
    before = compiler_preflight.fingerprint_files((("artifact", artifact),))
    compiler_preflight.verify_file_fingerprints(
        before, emit=print, pass_label="Workload postflight"
    )
    assert capsys.readouterr().out.strip() == "Workload postflight: PASS files=1"


def test_file_postflight_treats_deleted_input_as_mutation(tmp_path):
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"before")
    before = compiler_preflight.fingerprint_files((("artifact", artifact),))
    artifact.unlink()
    with pytest.raises(
        compiler_preflight.CompilerPreflightError,
        match="artifact changed after preflight.*does not exist",
    ):
        compiler_preflight.verify_file_fingerprints(before)


def test_probe_sends_compiler_and_quit_and_parses_actual_identity(
    tmp_path, monkeypatch
):
    engine = tmp_path / "engine.exe"
    engine.touch()
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=ATOMIC_COMPILER_OUTPUT)

    monkeypatch.setattr(compiler_preflight.subprocess, "run", fake_run)
    signature = compiler_preflight.probe_compilation_settings(
        engine, label="candidate", timeout=7
    )

    assert signature == BMI2_SIGNATURE
    assert observed["command"] == [str(engine.resolve())]
    assert observed["input"] == "compiler\nquit\n"
    assert observed["timeout"] == 7
    assert observed["stderr"] is subprocess.STDOUT


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (OSError("cannot execute"), "could not start"),
        (subprocess.TimeoutExpired("engine", 2), "timed out"),
    ],
)
def test_probe_reports_launch_and_timeout_errors(tmp_path, monkeypatch, failure, message):
    engine = tmp_path / "engine.exe"
    engine.touch()

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(compiler_preflight.subprocess, "run", fail)
    with pytest.raises(compiler_preflight.CompilerPreflightError, match=message):
        compiler_preflight.probe_compilation_settings(engine, label="baseline")


def test_bench_rejects_wrong_frozen_baseline_before_compiler_probe(
    tmp_path, monkeypatch, capsys
):
    candidate = tmp_path / "candidate.exe"
    baseline = tmp_path / "baseline.exe"
    net = tmp_path / "network.nnue"
    candidate.write_bytes(b"candidate")
    baseline.write_bytes(b"not frozen")
    net.write_bytes(b"net")
    called = False

    def compiler_probe(*args, **kwargs):
        nonlocal called
        called = True
        return BMI2_SIGNATURE

    monkeypatch.setattr(
        bench_compare, "require_matching_compilation_settings", compiler_probe
    )
    monkeypatch.setattr(
        bench_compare,
        "EXPECTED_NET_SHA256",
        compiler_preflight.fingerprint_file(net, label="net").sha256,
    )
    monkeypatch.setattr(
        bench_compare, "normative_psutil_fingerprints", lambda: ()
    )
    with pytest.raises(SystemExit) as exit_info:
        bench_compare.main(
            [
                "--candidate",
                str(candidate),
                "--baseline",
                str(baseline),
                "--eval-file",
                str(net),
                "--affinity",
                "0",
            ]
        )

    assert exit_info.value.code == 2
    assert "frozen Fairy-Stockfish" in capsys.readouterr().err
    assert not called


@pytest.mark.parametrize("mutated_label", ("candidate", "psutil_common"))
def test_bench_postflight_turns_asset_mutation_into_hard_error(
    tmp_path, monkeypatch, capsys, mutated_label
):
    candidate = tmp_path / "candidate.exe"
    baseline = tmp_path / "baseline.exe"
    net = tmp_path / "network.nnue"
    psutil_common = tmp_path / "psutil-common.py"
    candidate.write_bytes(b"candidate")
    baseline.write_bytes(b"baseline")
    net.write_bytes(b"net")
    psutil_common.write_bytes(b"psutil")

    baseline_sha = compiler_preflight.fingerprint_file(
        baseline, label="baseline"
    ).sha256
    net_sha = compiler_preflight.fingerprint_file(net, label="net").sha256
    monkeypatch.setattr(bench_compare, "NORMATIVE_BASELINE_SHA256", baseline_sha)
    monkeypatch.setattr(bench_compare, "EXPECTED_NET_SHA256", net_sha)
    monkeypatch.setattr(
        bench_compare, "require_matching_compilation_settings", lambda *a, **k: None
    )
    monkeypatch.setattr(
        bench_compare,
        "normative_psutil_fingerprints",
        lambda: (
            compiler_preflight.fingerprint_file(
                psutil_common, label="psutil_common"
            ),
        ),
    )
    monkeypatch.setattr(
        bench_compare.psutil,
        "Process",
        lambda *args, **kwargs: SimpleNamespace(cpu_affinity=lambda *args: [0]),
    )

    class FakeEngine:
        def __init__(self, label, *args):
            self.label = label

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def run_corpus(self, nodes):
            if self.label == "candidate":
                if mutated_label == "candidate":
                    candidate.write_bytes(b"mutated candidate")
                else:
                    psutil_common.write_bytes(b"mutated psutil")
            return bench_compare.Measurement(nodes=nodes, elapsed_ms=1)

    monkeypatch.setattr(bench_compare, "UciEngine", FakeEngine)

    with pytest.raises(SystemExit) as exit_info:
        bench_compare.main(
            [
                "--candidate",
                str(candidate),
                "--baseline",
                str(baseline),
                "--eval-file",
                str(net),
                "--affinity",
                "0",
            ]
        )
    assert exit_info.value.code == 2
    error = capsys.readouterr().err
    assert f"{mutated_label} changed after preflight" in error


def test_uci_engine_constructor_closes_child_when_initialization_fails(
    tmp_path, monkeypatch
):
    class FakeProcess:
        def __init__(self):
            self.pid = 123
            self.stdin = io.StringIO()
            self.stdout = []
            self.alive = True
            self.waited = False

        def poll(self):
            return None if self.alive else 0

        def wait(self, timeout=None):
            self.alive = False
            self.waited = True
            return 0

        def kill(self):
            self.alive = False

    process = FakeProcess()
    monkeypatch.setattr(
        bench_compare.subprocess, "Popen", lambda *args, **kwargs: process
    )

    class AffinityFailure:
        def __init__(self, pid):
            assert pid == process.pid

        def cpu_affinity(self, cpus):
            raise RuntimeError("affinity unavailable")

    monkeypatch.setattr(bench_compare.psutil, "Process", AffinityFailure)
    with pytest.raises(RuntimeError, match="affinity unavailable"):
        bench_compare.UciEngine(
            "candidate", tmp_path / "engine.exe", tmp_path / "net.nnue", 64, 0, 1.0
        )
    assert process.waited
    assert not process.alive
    assert process.stdin.getvalue() == "quit\n"


def test_uci_engine_rejects_silent_affinity_noop_and_closes_child(
    tmp_path, monkeypatch
):
    class FakeProcess:
        def __init__(self):
            self.pid = 123
            self.stdin = io.StringIO()
            self.stdout = []
            self.alive = True
            self.waited = False

        def poll(self):
            return None if self.alive else 0

        def wait(self, timeout=None):
            self.alive = False
            self.waited = True
            return 0

        def kill(self):
            self.alive = False

    process = FakeProcess()
    monkeypatch.setattr(
        bench_compare.subprocess, "Popen", lambda *args, **kwargs: process
    )

    class AffinityNoop:
        def __init__(self, pid):
            assert pid == process.pid

        def cpu_affinity(self, cpus=None):
            return [1] if cpus is None else None

    monkeypatch.setattr(bench_compare.psutil, "Process", AffinityNoop)
    with pytest.raises(RuntimeError, match="affinity readback mismatch"):
        bench_compare.UciEngine(
            "candidate", tmp_path / "engine.exe", tmp_path / "net.nnue", 64, 0, 1.0
        )
    assert process.waited
    assert not process.alive
    assert process.stdin.getvalue() == "quit\n"


def test_bench_nnue_output_requires_exact_enabled_nnue_path(tmp_path):
    net = (tmp_path / "atomic.nnue").resolve()
    bench_compare.require_nnue_output(
        "candidate",
        net,
        [
            "info string NNUE evaluation using Legacy Atomic V1 "
            f"{net} {bench_compare.CANDIDATE_NNUE_ARCHITECTURE_MARKER}",
            "readyok",
        ],
    )
    bench_compare.require_nnue_output(
        "baseline",
        net,
        [f"info string NNUE evaluation using {net} enabled", "readyok"],
    )
    bench_compare.require_nnue_output(
        "control",
        net,
        [
            "info string NNUE evaluation using AtomicNNUEV2 "
            f"{net} {bench_compare.CANDIDATE_NNUE_ARCHITECTURE_MARKER}",
            "readyok",
        ],
    )
    with pytest.raises(RuntimeError, match="did not confirm selected NNUE"):
        bench_compare.require_nnue_output(
            "candidate",
            net,
            ["info string classical evaluation enabled", "readyok"],
        )
    with pytest.raises(RuntimeError, match="NNUE/protocol error"):
        bench_compare.require_nnue_output(
            "candidate",
            net,
            ["info string ERROR: incompatible net", "readyok"],
        )


@pytest.mark.parametrize(
    ("argument", "value", "message"),
    (
        ("--nodes", "99999", "--nodes must be exactly"),
        ("--hash", "32", "--hash must be exactly"),
    ),
)
def test_commit_ab_requires_normative_workload_before_preflight(
    tmp_path, capsys, argument, value, message
):
    candidate = tmp_path / "candidate.exe"
    control = tmp_path / "control.exe"
    net = tmp_path / "atomic.nnue"
    candidate.write_bytes(b"candidate")
    control.write_bytes(b"control")
    net.write_bytes(b"net")

    with pytest.raises(SystemExit) as exit_info:
        bench_ab.main(
            [
                "--candidate",
                str(candidate),
                "--control",
                str(control),
                "--eval-file",
                str(net),
                "--affinity",
                "0",
                argument,
                value,
            ]
        )
    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err


def test_commit_ab_rejects_byte_identical_engines():
    artifacts = {
        "candidate": SimpleNamespace(sha256="A" * 64),
        "baseline": SimpleNamespace(sha256="A" * 64),
    }
    with pytest.raises(
        bench_ab.GateConfigurationError, match="identical SHA-256"
    ):
        bench_ab.require_distinct_engine_artifacts(artifacts)


@pytest.mark.parametrize("pipe_error", (BrokenPipeError, OSError, ValueError))
def test_uci_engine_close_reaps_child_after_pipe_error(pipe_error):
    class FailingInput:
        def write(self, _value):
            raise pipe_error("closed input")

        def flush(self):
            raise AssertionError("flush must not run after write fails")

    class FakeProcess:
        def __init__(self):
            self.stdin = FailingInput()
            self.alive = True
            self.waited = False
            self.killed = False

        def poll(self):
            return None if self.alive else 0

        def wait(self, timeout=None):
            assert timeout == 1.0
            self.alive = False
            self.waited = True
            return 0

        def kill(self):
            self.alive = False
            self.killed = True

    process = FakeProcess()
    engine = object.__new__(bench_compare.UciEngine)
    engine.process = process
    engine.timeout = 1.0
    engine.close()

    assert process.waited
    assert not process.alive
    assert not process.killed


def test_bench_nnue_preflight_accepts_candidate_marker_emitted_on_go(tmp_path):
    net = (tmp_path / "atomic.nnue").resolve()
    commands = []
    engine = object.__new__(bench_compare.UciEngine)
    engine.label = "candidate"
    engine.ready = lambda: ["readyok"]
    engine.send = commands.append

    search_output = [
        "info string NNUE evaluation using Legacy Atomic V1 "
        f"{net} {bench_compare.CANDIDATE_NNUE_ARCHITECTURE_MARKER}",
        "info depth 1 nodes 1 time 1",
        "bestmove b2b3",
    ]

    def read_until(predicate):
        assert predicate(search_output[-1])
        return list(search_output)

    engine.read_until = read_until
    engine.verify_nnue(net)

    assert commands == ["position startpos", "go nodes 1"]


def test_bench_infrastructure_failure_exits_two(tmp_path, monkeypatch, capsys):
    candidate = tmp_path / "candidate.exe"
    baseline = tmp_path / "baseline.exe"
    net = tmp_path / "network.nnue"
    for path in (candidate, baseline, net):
        path.write_bytes(path.name.encode("ascii"))
    monkeypatch.setattr(
        bench_compare,
        "NORMATIVE_BASELINE_SHA256",
        compiler_preflight.fingerprint_file(baseline, label="baseline").sha256,
    )
    monkeypatch.setattr(
        bench_compare,
        "EXPECTED_NET_SHA256",
        compiler_preflight.fingerprint_file(net, label="net").sha256,
    )
    monkeypatch.setattr(
        bench_compare, "require_matching_compilation_settings", lambda *a, **k: None
    )
    monkeypatch.setattr(
        bench_compare, "normative_psutil_fingerprints", lambda: ()
    )
    monkeypatch.setattr(
        bench_compare.psutil,
        "Process",
        lambda *args, **kwargs: SimpleNamespace(cpu_affinity=lambda *args: [0]),
    )

    class BrokenEngine:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("cannot start engine")

    monkeypatch.setattr(bench_compare, "UciEngine", BrokenEngine)
    with pytest.raises(SystemExit) as exit_info:
        bench_compare.main(
            [
                "--candidate",
                str(candidate),
                "--baseline",
                str(baseline),
                "--eval-file",
                str(net),
                "--affinity",
                "0",
            ]
        )
    assert exit_info.value.code == 2
    assert "benchmark infrastructure failure" in capsys.readouterr().err


@pytest.mark.parametrize(
    "drift",
    (
        ["--nodes", "99999", "--affinity", "0"],
        ["--hash", "32", "--affinity", "0"],
        [],
    ),
)
def test_bench_rejects_non_normative_workload_before_preflight(
    tmp_path, monkeypatch, drift
):
    candidate = tmp_path / "candidate.exe"
    baseline = tmp_path / "baseline.exe"
    net = tmp_path / "network.nnue"
    for path in (candidate, baseline, net):
        path.write_bytes(path.name.encode("ascii"))
    called = False

    def compiler_probe(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(
        bench_compare, "require_matching_compilation_settings", compiler_probe
    )
    monkeypatch.setattr(
        bench_compare, "normative_psutil_fingerprints", lambda: ()
    )
    with pytest.raises(SystemExit) as exit_info:
        bench_compare.main(
            [
                "--candidate",
                str(candidate),
                "--baseline",
                str(baseline),
                "--eval-file",
                str(net),
                *drift,
            ]
        )
    assert exit_info.value.code == 2
    assert not called


def test_benchmark_json_is_exact_recomputable_and_canonical(tmp_path):
    candidate = tmp_path / "Atomic-Stockfish-bmi2.exe"
    baseline = tmp_path / "FSF-bmi2.exe"
    net = tmp_path / "atomic.nnue"
    candidate.write_bytes(b"candidate")
    baseline.write_bytes(b"baseline")
    net.write_bytes(b"net")
    candidate_fingerprint = compiler_preflight.fingerprint_file(
        candidate, label="candidate"
    )
    baseline_fingerprint = compiler_preflight.fingerprint_file(
        baseline, label="baseline"
    )
    net_fingerprint = compiler_preflight.fingerprint_file(net, label="eval_file")
    candidate_samples = [
        bench_compare.Measurement(1_300_000, value)
        for value in (1000, 1100, 900, 1050, 950)
    ]
    baseline_samples = [
        bench_compare.Measurement(1_300_000, value)
        for value in (1300, 1200, 1400, 1250, 1350)
    ]

    document = bench_compare.benchmark_document(
        candidate_fingerprint=candidate_fingerprint,
        baseline_fingerprint=baseline_fingerprint,
        eval_fingerprint=net_fingerprint,
        candidate_samples=candidate_samples,
        baseline_samples=baseline_samples,
        affinity=7,
    )

    assert set(document) == {
        "baseline",
        "baselineMedianNps",
        "baselineSamples",
        "candidate",
        "candidateMedianNps",
        "candidateSamples",
        "corpusSha256",
        "cpuAffinity",
        "evalFileSha256",
        "gate",
        "hashMb",
        "metric",
        "nodesPerFen",
        "pass",
        "positions",
        "ratio",
        "repetitions",
        "schemaVersion",
        "searchTimeoutSeconds",
        "threads",
        "warmups",
    }
    assert document["candidateMedianNps"] == "1300000.000000"
    assert document["baselineMedianNps"] == "1000000.000000"
    assert document["ratio"] == "1.300000"
    assert document["pass"] is True
    assert document["searchTimeoutSeconds"] == 60
    assert document["candidate"] == {
        "bytes": len(b"candidate"),
        "fileName": candidate.name,
        "sha256": candidate_fingerprint.sha256.lower(),
    }
    assert document["corpusSha256"] == (
        "2738065a8a70d61da46fa3c75f95d645e50e601b43792df0e7b3cc97b1d891a1"
    )

    output = tmp_path / "benchmark.json"
    bench_compare.write_benchmark_json(output, document)
    expected = (
        json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    assert output.read_bytes() == expected
    with pytest.raises(FileExistsError):
        bench_compare.write_benchmark_json(output, document)
    assert output.read_bytes() == expected


def test_benchmark_json_rejects_wrong_sample_count():
    with pytest.raises(ValueError, match="exactly 5"):
        bench_compare.median_nps(
            [bench_compare.Measurement(1000, 1)] * 4
        )


def test_benchmark_refuses_to_overwrite_json_before_engine_preflight(
    tmp_path, monkeypatch
):
    candidate = tmp_path / "candidate.exe"
    baseline = tmp_path / "baseline.exe"
    net = tmp_path / "network.nnue"
    output = tmp_path / "benchmark.json"
    for path in (candidate, baseline, net):
        path.write_bytes(path.name.encode("ascii"))
    original = b"operator-owned\n"
    output.write_bytes(original)
    called = False

    def compiler_probe(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(
        bench_compare, "require_matching_compilation_settings", compiler_probe
    )
    with pytest.raises(SystemExit) as exit_info:
        bench_compare.main(
            [
                "--candidate",
                str(candidate),
                "--baseline",
                str(baseline),
                "--eval-file",
                str(net),
                "--affinity",
                "0",
                "--json-output",
                str(output),
            ]
        )
    assert exit_info.value.code == 2
    assert output.read_bytes() == original
    assert not called


def test_scoped_gate_scripts_parse_as_python_39():
    for script in (
        TESTS_DIR / "atomic_compiler_preflight.py",
        TESTS_DIR / "atomic_los_gate.py",
        TESTS_DIR / "atomic_bench_compare.py",
        TESTS_DIR / "atomic_bench_ab.py",
    ):
        ast.parse(script.read_text(encoding="utf-8"), filename=str(script), feature_version=(3, 9))
