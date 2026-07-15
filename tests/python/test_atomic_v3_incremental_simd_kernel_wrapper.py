from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Dict, Iterable, Mapping, Sequence, Tuple

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tests" / "atomic_v3_incremental_simd_kernel.py"
SPEC = importlib.util.spec_from_file_location(
    "atomic_v3_incremental_simd_kernel_wrapper", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
KERNEL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = KERNEL
SPEC.loader.exec_module(KERNEL)


def _record(
    kind: str, fields: Iterable[Tuple[str, object]], end_key: str, end_value: object
) -> str:
    return "\n".join(
        [f"record={kind}"]
        + [f"{key}={value}" for key, value in fields]
        + [f"{end_key}={end_value}"]
    )


def _summary(required_isa: str, maximum_isa: str) -> Dict[str, object]:
    return {
        "isa.requested": required_isa,
        "isa.executed": required_isa,
        "tail_cases": len(KERNEL.TAIL_COUNTS),
        "dispatcher_calls": KERNEL.DISPATCHER_CALLS,
        "stable_calls": KERNEL.STABLE_CALLS,
        "null_pointer_probes": 3,
        "unavailable_probes": KERNEL._unavailable_probes(maximum_isa),
        "fallback_calls": 0,
        "canary_checks": KERNEL.CANARY_CHECKS,
        "source.minimum_covered": 1,
        "source.maximum_covered": 1,
        "nonzero_base_lanes": KERNEL.NONZERO_BASE_LANES,
        "fingerprint": KERNEL.FROZEN_FINGERPRINT,
    }


def _sentinel(summary: Mapping[str, object]) -> str:
    return (
        "AtomicNNUEV3 incremental SIMD kernel gate passed: "
        f"requested={summary['isa.requested']} executed={summary['isa.executed']} "
        f"tail-cases={summary['tail_cases']} "
        f"dispatcher-calls={summary['dispatcher_calls']} "
        f"stable-calls={summary['stable_calls']} "
        f"null-pointer-probes={summary['null_pointer_probes']} "
        f"unavailable-probes={summary['unavailable_probes']} "
        f"fallback-calls={summary['fallback_calls']} "
        f"canary-checks={summary['canary_checks']} "
        f"nonzero-base-lanes={summary['nonzero_base_lanes']} "
        f"fingerprint={summary['fingerprint']}"
    )


def _valid_output(required_isa: str = "avx2", maximum_isa: str = "avx2") -> str:
    identity = _record(
        "incremental_simd_kernel_identity",
        (
            ("isa.requested", required_isa),
            ("isa.maximum", maximum_isa),
            ("tail_counts", ",".join(str(value) for value in KERNEL.TAIL_COUNTS)),
        ),
        "end_identity",
        1,
    )
    tails = []
    for case_index, count in enumerate(KERNEL.TAIL_COUNTS):
        fields = (
            ("case", case_index),
            ("count", count),
            ("isa.executed", required_isa),
            ("dispatcher.add.exact", 1),
            ("dispatcher.remove.exact", 1),
            ("dispatcher.restore.exact", 1),
            ("stable.add.exact", 1),
            ("stable.remove.exact", 1),
            ("stable.restore.exact", 1),
            ("destination.canaries", 2),
            ("source.canaries", 2),
            ("source.immutable", 1),
            ("source.minimum_covered", int(count >= 1)),
            ("source.maximum_covered", int(count >= 2)),
            ("nonzero_bases", count),
            ("comparison.exact", 1),
        )
        tails.append(
            _record(
                "incremental_simd_kernel_tail",
                fields,
                "end_case",
                case_index,
            )
        )
    summary = _summary(required_isa, maximum_isa)
    summary_record = _record(
        "incremental_simd_kernel_summary",
        ((key, summary[key]) for key in KERNEL.SUMMARY_FIELDS),
        "end_summary",
        1,
    )
    return "\n".join([identity] + tails + [summary_record, _sentinel(summary)])


def test_frozen_contract_and_python39_grammar() -> None:
    assert KERNEL.REQUIRED_ISAS == ("scalar", "sse41", "avx2")
    assert KERNEL.TAIL_COUNTS == (
        0,
        1,
        3,
        4,
        7,
        8,
        9,
        15,
        16,
        17,
        1023,
        1024,
        1025,
    )
    assert KERNEL.DISPATCHER_CALLS == 54
    assert KERNEL.STABLE_CALLS == 54
    assert KERNEL.CANARY_CHECKS == 312
    assert KERNEL.NONZERO_BASE_LANES == sum(KERNEL.TAIL_COUNTS)
    assert KERNEL._source_value(0) == -32768
    assert KERNEL._source_value(1) == 32767
    assert all(KERNEL._base_value(index) != 0 for index in range(1025))
    assert KERNEL.FROZEN_FINGERPRINT == "0x21E9FF9A77F881F2"
    assert KERNEL.expected_fingerprint() == KERNEL.FROZEN_FINGERPRINT
    ast.parse(MODULE_PATH.read_text(encoding="utf-8"), feature_version=9)
    ast.parse(Path(__file__).read_text(encoding="utf-8"), feature_version=9)


@pytest.mark.parametrize(
    ("required_isa", "maximum_isa", "unavailable_probes"),
    (("scalar", "scalar", 6), ("sse41", "sse41", 4), ("avx2", "avx2", 2)),
)
def test_strict_gate_accepts_each_exact_isa(
    required_isa: str, maximum_isa: str, unavailable_probes: int
) -> None:
    output = _valid_output(required_isa, maximum_isa)
    parsed = KERNEL.parse_gate_output(output)
    assert int(parsed.summary["unavailable_probes"]) == unavailable_probes
    assert KERNEL.validate_gate_output(output, required_isa) == KERNEL.FROZEN_FINGERPRINT


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda _value: "", "no output"),
        (
            lambda value: value.replace("dispatcher.remove.exact=1\n", "", 1),
            "ordered field mismatch",
        ),
        (
            lambda value: value.replace(
                "dispatcher.add.exact=1\n",
                "dispatcher.add.exact=1\ndispatcher.add.exact=1\n",
                1,
            ),
            "ordered field mismatch",
        ),
        (lambda value: value.replace("case=0", " case=0", 1), "blank or padded"),
        (lambda value: value + "\ntrailing", "sole final output line"),
        (lambda value: value.rsplit("\n", 1)[0], "success sentinel"),
    ),
)
def test_parser_rejects_empty_truncated_duplicate_padded_or_trailing_output(
    mutate: object, message: str
) -> None:
    broken = mutate(_valid_output())  # type: ignore[operator]
    with pytest.raises(KERNEL.KernelGateError, match=message):
        KERNEL.parse_gate_output(broken)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    (
        ("isa.executed=avx2", "isa.executed=sse41", "isa.executed differs"),
        ("count=0", "count=2", "count differs"),
        ("dispatcher.add.exact=1", "dispatcher.add.exact=0", "dispatcher.add.exact differs"),
        ("stable.restore.exact=1", "stable.restore.exact=0", "stable.restore.exact differs"),
        ("destination.canaries=2", "destination.canaries=1", "destination.canaries differs"),
        ("source.immutable=1", "source.immutable=0", "source.immutable differs"),
        ("source.minimum_covered=0", "source.minimum_covered=1", "minimum_covered differs"),
        ("nonzero_bases=0", "nonzero_bases=1", "nonzero_bases differs"),
        ("fallback_calls=0", "fallback_calls=1", "fallback_calls differs"),
        ("canary_checks=312", "canary_checks=311", "canary_checks differs"),
        (
            f"fingerprint={KERNEL.FROZEN_FINGERPRINT}",
            "fingerprint=0x0000000000000000",
            "fingerprint differs",
        ),
    ),
)
def test_validator_rejects_semantic_drift(old: str, new: str, message: str) -> None:
    broken = _valid_output().replace(old, new, 1)
    with pytest.raises(KERNEL.KernelGateError, match=message):
        KERNEL.validate_gate_output(broken, "avx2")


def test_validator_rejects_unavailable_request_and_sentinel_summary_drift() -> None:
    with pytest.raises(KERNEL.KernelGateError, match="exceeds compiled maximum"):
        KERNEL.validate_gate_output(_valid_output("avx2", "scalar"), "avx2")

    output = _valid_output().replace("fallback-calls=0", "fallback-calls=1", 1)
    with pytest.raises(KERNEL.KernelGateError, match="sentinel fallback_calls differs"):
        KERNEL.validate_gate_output(output, "avx2")


def test_run_uses_one_exact_isa_command_and_validates_zero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = tmp_path / "incremental-kernel-runner"
    runner.write_bytes(b"runner")
    observed: Dict[str, object] = {}

    def fake_run(command: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, _valid_output("sse41", "sse41"), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert KERNEL.run(runner, "sse41", 12.5) == KERNEL.FROZEN_FINGERPRINT
    assert observed["command"] == [str(runner.resolve()), "--require-isa", "sse41"]
    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["timeout"] == 12.5
    assert kwargs["check"] is False


def test_run_rejects_wrong_zero_exit_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = tmp_path / "wrong-runner"
    runner.write_bytes(b"runner")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Unknown command: --require-isa\n", stderr=""
        ),
    )
    with pytest.raises(KERNEL.KernelGateError, match="expected record"):
        KERNEL.run(runner, "scalar", 1.0)


def test_main_reports_validation_failure_instead_of_accepting_a_claim(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        KERNEL,
        "parse_args",
        lambda: SimpleNamespace(
            runner=Path("wrong-runner"), require_isa="avx2", timeout=1.0
        ),
    )
    monkeypatch.setattr(
        KERNEL,
        "run",
        lambda *_args: (_ for _ in ()).throw(
            KERNEL.KernelGateError("success sentinel missing")
        ),
    )
    assert KERNEL.main() == 1
    assert "success sentinel missing" in capsys.readouterr().err
