from __future__ import annotations

import ast
import hashlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Dict, Mapping, Tuple

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tests" / "atomic_v3_simd_differential.py"
SPEC = importlib.util.spec_from_file_location("atomic_v3_simd_differential", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
SIMD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SIMD
SPEC.loader.exec_module(SIMD)


def _diagnostic(seed: int = 0) -> Dict[str, object]:
    result: Dict[str, object] = {
        key: seed + index for index, key in enumerate(sorted(SIMD.SCALAR_INTEGER_KEYS))
    }
    result.update(
        {
            key: (seed + index, seed + index + 1)
            for index, key in enumerate(sorted(SIMD.SCALAR_ARRAY_KEYS))
        }
    )
    return result


def _case_fields(
    expected: Mapping[str, object], *, simd_delta: int = 0
) -> Dict[str, str]:
    fingerprint = SIMD.diagnostic_fingerprint(expected)
    fields = {
        "case": "0",
        "fen": "7k/8/8/8/8/8/8/K7 w - - 0 1",
        "chess960": "0",
        "isa.requested": "avx2",
        "isa.executed": "avx2",
        "status": "ok",
        "comparison.exact": "1",
        "scalar.fingerprint": fingerprint,
        "simd.fingerprint": fingerprint,
    }
    for prefix in ("scalar.", "simd."):
        for key, value in expected.items():
            if isinstance(value, tuple):
                encoded = ",".join(str(item) for item in value)
            else:
                encoded = str(value)
            fields[prefix + key] = encoded
    fields.update(_counter_fields("simd.counters.", SIMD.expected_accounting((expected,))))
    if simd_delta:
        key = "simd.raw_output"
        fields[key] = str(int(fields[key]) + simd_delta)
    return fields


def _counter_fields(
    prefix: str, accounting: object, isa: str = "avx2"
) -> Dict[str, str]:
    calls = {name: 0 for name in SIMD.REQUIRED_ISAS}
    calls[isa] = accounting.kernel_calls
    values = {
        "bias_i16_rows": accounting.bias_rows,
        "hm_i16_rows": accounting.hm_rows,
        "capture_pair_i8_rows": accounting.capture_pair_rows,
        "king_blast_ep_i16_rows": accounting.king_blast_ep_rows,
        "blast_ring_i8_rows": accounting.blast_ring_rows,
        "i16_rows": accounting.i16_rows,
        "i8_rows": accounting.i8_rows,
        "i16_lanes": accounting.i16_rows * 1024,
        "i8_lanes": accounting.i8_rows * 1024,
        "scalar_kernel_calls": calls["scalar"],
        "sse41_kernel_calls": calls["sse41"],
        "avx2_kernel_calls": calls["avx2"],
        "kernel_calls": accounting.kernel_calls,
        "fallback_calls": 0,
    }
    return {prefix + key: str(value) for key, value in values.items()}


def _probe_fields(index: int, name: str, isa: str = "avx2") -> Dict[str, str]:
    weights = SIMD.WIDENING_INPUTS[name]
    before = SIMD.WIDENING_BEFORE
    expected = tuple(left + right for left, right in zip(before, weights))
    encoded = lambda values: ",".join(str(item) for item in values)
    return {
        "probe": str(index),
        "kind": name,
        "isa.executed": isa,
        "before": encoded(before),
        "input": encoded(weights),
        "expected": encoded(expected),
        "actual": encoded(expected),
        "tail_counts": encoded(SIMD.TAIL_PROBE_COUNTS),
        "kernel_calls": str(len(SIMD.TAIL_PROBE_COUNTS) + 1),
        "fallback_calls": "0",
        "tail_cases": str(len(SIMD.TAIL_PROBE_COUNTS)),
        "tail_canaries": str(len(SIMD.TAIL_PROBE_COUNTS) * 2),
        "tails.exact": "1",
        "comparison.exact": "1",
    }


def _summary(isa: str = "avx2", **overrides: str) -> Dict[str, str]:
    accounting = SIMD.RowAccounting(109, 2, 3, 7, 5, 11)
    value = {
        "isa.requested": isa,
        "isa.executed": isa,
        "cases": str(SIMD.TOTAL_POSITIONS),
        "comparisons": str(SIMD.TOTAL_POSITIONS),
        "errors": "0",
        "error_probes": "4",
        "widening_probes": "2",
        "corpus_fingerprint": "0x0123456789ABCDEF",
    }
    value.update(_counter_fields("totals.", accounting, isa))
    value.update(overrides)
    return value


def _sentinel(summary: Mapping[str, str]) -> str:
    return (
        "AtomicNNUEV3 SIMD gate passed: "
        f"requested={summary['isa.requested']} executed={summary['isa.executed']} "
        f"cases={summary['cases']} comparisons={summary['comparisons']} "
        f"errors={summary['errors']} error_probes={summary['error_probes']} "
        f"widening_probes={summary['widening_probes']} "
        f"i16_rows={summary['totals.i16_rows']} "
        f"i8_rows={summary['totals.i8_rows']} "
        f"kernel_calls={summary['totals.kernel_calls']} "
        f"fallback_calls={summary['totals.fallback_calls']} "
        f"fingerprint={summary['corpus_fingerprint']}"
    )


def _record(kind: str, fields: Mapping[str, str], end: str) -> str:
    return "\n".join(
        [f"record={kind}"] + [f"{key}={value}" for key, value in fields.items()] + [end]
    )


def _output(*, duplicate_case_key: bool = False) -> str:
    expected = _diagnostic()
    case_fields = _case_fields(expected)
    case = _record("simd_case", case_fields, "end_case=0")
    if duplicate_case_key:
        case = case.replace("case=0\n", "case=0\ncase=0\n", 1)
    probes = [
        _record("simd_widening_probe", _probe_fields(index, name), f"end_probe={index}")
        for index, name in enumerate(SIMD.WIDENING_PROBES)
    ]
    identity = _record(
        "simd_identity",
        {
            "network.version": "0xA70C0003",
            "network.hash": "0xCF9A484",
            "network.description": "fixture",
            "wire.policy": "identity",
            "wire.simd_permuted": "1",
            "isa.requested": "avx2",
            "isa.maximum": "avx2",
        },
        "end_identity=1",
    )
    error_values = (
        ("invalid_side", 2, 1, 2),
        ("missing_black_king", 2, 1, 4),
        ("multiple_white_kings", 2, 1, 5),
        ("unsupported_isa", 1, 0, 0),
    )
    errors = []
    for index, (name, simd_error, scalar_error, feature_error) in enumerate(error_values):
        errors.append(
            _record(
                "simd_error_probe",
                {
                    "error_probe": str(index),
                    "name": name,
                    "actual.error": str(simd_error),
                    "actual.scalar_error": str(scalar_error),
                    "actual.feature_error": str(feature_error),
                    "transactional": "1",
                    "comparison.exact": "1",
                },
                f"end_error_probe={index}",
            )
        )
    summary = _summary()
    return "\n".join(
        [identity]
        + probes
        + errors
        + [case, _record("simd_summary", summary, "end_summary=1"), _sentinel(summary)]
    )


def test_frozen_contract_and_python39_grammar() -> None:
    assert SIMD.FROZEN_NET_BYTES == 77_349_879
    assert SIMD.FROZEN_NET_SHA256 == (
        "00e46223822d06d7927e884eec10739ba19ef8dd82a6e262f627d361658080c2"
    )
    assert SIMD.FROZEN_POSITIONS == 102
    assert SIMD.SUPPLEMENTAL_POSITIONS == 7
    assert SIMD.TOTAL_POSITIONS == 109
    assert SIMD.WIDENING_PROBES == ("i16_signed", "i8_signed")
    assert SIMD.TAIL_PROBE_COUNTS == (0, 1, 3, 4, 7, 8, 15, 16, 17)
    assert SIMD.FROZEN_ROW_COUNTS == (218, 3_190, 2_564, 504, 2_992)
    assert SIMD.FROZEN_CORPUS_FINGERPRINT == "0x4FBDB31B354FC080"
    ast.parse(MODULE_PATH.read_text(encoding="utf-8"), feature_version=9)
    ast.parse(Path(__file__).read_text(encoding="utf-8"), feature_version=9)


def test_corpus_and_batch_wire_are_frozen() -> None:
    cases = SIMD.corpus()
    assert len(cases) == 109
    assert tuple(case.index for case in cases) == tuple(range(109))
    assert any(case.chess960 for case in cases)
    assert any(case.position.ep_square is not None for case in cases)
    encoded = SIMD.encode_batch(cases)
    assert encoded.count("record=simd_input\n") == 109
    assert encoded.endswith("batch_cases=109\n")
    assert "\ncase=108\n" in encoded
    assert hashlib.sha256(encoded.encode("utf-8")).hexdigest() == SIMD.FROZEN_BATCH_SHA256


def test_gate_parser_accepts_one_strictly_framed_case() -> None:
    parsed = SIMD.parse_gate_output(_output(), expected_cases=1, required_isa="avx2")
    assert len(parsed.cases) == 1
    assert len(parsed.probes) == 2
    assert parsed.summary["cases"] == "109"


def test_gate_parser_rejects_a_record_truncated_before_its_end_marker() -> None:
    complete = _output()
    output = (
        complete.split("end_case=0", 1)[0].rstrip("\n")
        + "\n"
        + complete.splitlines()[-1]
    )
    with pytest.raises(SIMD.DifferentialFailure, match="truncated before 'end_case=0'"):
        SIMD.parse_gate_output(output, expected_cases=1, required_isa="avx2")


def test_gate_parser_rejects_a_duplicated_success_sentinel() -> None:
    output = _output()
    output += "\n" + output.splitlines()[-1]
    with pytest.raises(SIMD.DifferentialFailure, match="output after its summary"):
        SIMD.parse_gate_output(output, expected_cases=1, required_isa="avx2")


@pytest.mark.parametrize(
    ("output", "message"),
    (
        ("", "no output"),
        (_output().split("end_case=0", 1)[0], "final non-empty line"),
        (_output(duplicate_case_key=True), "duplicate field"),
        (_output() + "\ntrailing", "final non-empty line"),
        (
            _output().replace("cases=109\n", "cases=109\ncases=109\n", 1),
            "malformed/duplicate field",
        ),
    ),
)
def test_gate_parser_rejects_empty_truncated_duplicate_or_trailing_output(
    output: str, message: str
) -> None:
    with pytest.raises(SIMD.DifferentialFailure, match=message):
        SIMD.parse_gate_output(output, expected_cases=1, required_isa="avx2")


def test_gate_parser_rejects_wrong_executed_isa() -> None:
    output = _output().replace("executed=avx2", "executed=sse41")
    with pytest.raises(SIMD.DifferentialFailure, match="execute the required ISA"):
        SIMD.parse_gate_output(output, expected_cases=1, required_isa="avx2")


def test_complete_diagnostic_matches_scalar_simd_and_python() -> None:
    expected = _diagnostic()
    position = SIMD.corpus()[0]
    fields = _case_fields(expected)
    fields["fen"] = position.fen
    fields["chess960"] = str(int(position.chess960))
    record = SIMD.CaseRecord(0, fields)
    SIMD._verify_case(record, position, expected, "avx2")

    broken = dict(fields)
    broken["simd.raw_output"] = str(int(broken["simd.raw_output"]) + 1)
    with pytest.raises(SIMD.DifferentialFailure, match="SIMD/Python"):
        SIMD._verify_case(SIMD.CaseRecord(0, broken), position, expected, "avx2")


def test_nested_simd_counters_do_not_pollute_diagnostic_inventory() -> None:
    expected = _diagnostic()
    fields = _case_fields(expected)
    parsed = SIMD._diagnostic(fields, "simd.")
    assert parsed == expected


def test_widening_probes_recompute_signed_boundaries_in_python() -> None:
    probes = tuple(
        SIMD.ProbeRecord(index, _probe_fields(index, name))
        for index, name in enumerate(SIMD.WIDENING_PROBES)
    )
    SIMD._verify_probes(probes, "avx2")
    broken = dict(probes[0].fields)
    broken["actual"] = "1,2"
    with pytest.raises(SIMD.DifferentialFailure, match="incoherent lane"):
        SIMD._verify_probes(
            (SIMD.ProbeRecord(0, broken), probes[1]), "avx2"
        )


@pytest.mark.parametrize(
    ("key", "value", "message"),
    (
        ("tail_counts", "0,1,3,4,7,8,15,16", "tail boundary list differs"),
        ("kernel_calls", "9", "kernel_calls differs"),
        ("fallback_calls", "1", "fallback_calls differs"),
        ("tail_cases", "8", "tail_cases differs"),
        ("tail_canaries", "17", "tail_canaries differs"),
        ("tails.exact", "0", "tails.exact differs"),
    ),
)
def test_widening_probes_reject_tail_boundary_counter_or_canary_drift(
    key: str, value: str, message: str
) -> None:
    fields = _probe_fields(0, "i16_signed")
    fields[key] = value
    other = SIMD.ProbeRecord(1, _probe_fields(1, "i8_signed"))
    with pytest.raises(SIMD.DifferentialFailure, match=message):
        SIMD._verify_probes((SIMD.ProbeRecord(0, fields), other), "avx2")


def test_widening_probe_field_order_is_part_of_the_wire_contract() -> None:
    original = _probe_fields(0, "i16_signed")
    items = list(original.items())
    items[7], items[8] = items[8], items[7]
    reordered = dict(items)
    other = SIMD.ProbeRecord(1, _probe_fields(1, "i8_signed"))
    with pytest.raises(SIMD.DifferentialFailure, match="ordered field inventory"):
        SIMD._verify_probes((SIMD.ProbeRecord(0, reordered), other), "avx2")


def test_counter_accounting_is_fail_closed() -> None:
    summary = _summary()
    sentinel_match = SIMD.FINAL_SENTINEL_RE.fullmatch(_sentinel(summary))
    assert sentinel_match is not None
    output = SIMD.GateOutput({}, (), (), (), summary, sentinel_match.groupdict())
    accounting = SIMD.RowAccounting(109, 2, 3, 7, 5, 11)
    SIMD._verify_accounting(output, "avx2", accounting, "0x0123456789ABCDEF")

    bad_rows = dict(summary)
    bad_rows["totals.i16_rows"] = "9"
    bad_output = SIMD.GateOutput({}, (), (), (), bad_rows, sentinel_match.groupdict())
    with pytest.raises(SIMD.DifferentialFailure, match="i16_rows differs"):
        SIMD._verify_accounting(
            bad_output, "avx2", accounting, "0x0123456789ABCDEF"
        )

    bad_calls = dict(summary)
    bad_calls["totals.sse41_kernel_calls"] = "1"
    bad_output = SIMD.GateOutput({}, (), (), (), bad_calls, sentinel_match.groupdict())
    with pytest.raises(SIMD.DifferentialFailure, match="sse41_kernel_calls differs"):
        SIMD._verify_accounting(
            bad_output, "avx2", accounting, "0x0123456789ABCDEF"
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"cases": "108"}, "case accounting"),
        ({"comparisons": "108"}, "case accounting"),
        ({"errors": "1"}, "case accounting"),
        ({"totals.i8_lanes": "1"}, "i8_lanes differs"),
        ({"totals.fallback_calls": "1"}, "fallback_calls differs"),
    ),
)
def test_incompatible_summary_counters_are_rejected(
    overrides: Mapping[str, str], message: str
) -> None:
    summary = _summary(**overrides)
    sentinel_match = SIMD.FINAL_SENTINEL_RE.fullmatch(_sentinel(summary))
    assert sentinel_match is not None
    output = SIMD.GateOutput({}, (), (), (), summary, sentinel_match.groupdict())
    with pytest.raises(SIMD.DifferentialFailure, match=message):
        SIMD._verify_accounting(
            output,
            "avx2",
            SIMD.RowAccounting(109, 2, 3, 7, 5, 11),
            "0x0123456789ABCDEF",
        )


def test_fixture_authentication_checks_size_and_sha_from_one_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fixture = tmp_path / "atomic-v3.nnue"
    payload = b"private-simd-fixture"
    fixture.write_bytes(payload)
    monkeypatch.setattr(SIMD, "FROZEN_NET_BYTES", len(payload))
    monkeypatch.setattr(SIMD, "FROZEN_NET_SHA256", hashlib.sha256(payload).hexdigest())
    assert SIMD.authenticate_fixture(fixture) == hashlib.sha256(payload).hexdigest()

    monkeypatch.setattr(SIMD, "FROZEN_NET_BYTES", len(payload) + 1)
    with pytest.raises(SIMD.FixtureAuthenticationError, match="size mismatch"):
        SIMD.authenticate_fixture(fixture)
    monkeypatch.setattr(SIMD, "FROZEN_NET_BYTES", len(payload))
    monkeypatch.setattr(SIMD, "FROZEN_NET_SHA256", "0" * 64)
    with pytest.raises(SIMD.FixtureAuthenticationError, match="SHA-256 mismatch"):
        SIMD.authenticate_fixture(fixture)


def test_main_reports_failure_instead_of_accepting_a_zero_exit_claim(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        SIMD,
        "parse_args",
        lambda: SimpleNamespace(
            runner=Path("wrong-runner"),
            net=Path("wrong-net"),
            require_isa="avx2",
            timeout=1.0,
        ),
    )
    monkeypatch.setattr(
        SIMD,
        "run",
        lambda *_args: (_ for _ in ()).throw(
            SIMD.DifferentialFailure("success sentinel missing")
        ),
    )
    assert SIMD.main() == 1
    assert "success sentinel missing" in capsys.readouterr().err
