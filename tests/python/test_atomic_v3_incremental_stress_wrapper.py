from __future__ import annotations

import ast
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tests" / "atomic_v3_incremental_stress.py"
SPEC = importlib.util.spec_from_file_location(
    "atomic_v3_incremental_stress_wrapper", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
WRAPPER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = WRAPPER
SPEC.loader.exec_module(WRAPPER)


def sentinel(
    *,
    mode: str = "smoke",
    requested: int = 4_096,
    actual: Optional[int] = None,
    makes: Optional[int] = None,
    undos: Optional[int] = None,
    evaluations: Optional[int] = None,
    full_refresh: Optional[int] = None,
    captures: int = 120,
    terminal_failures: int = 3,
    standard_castles: int = 2,
    atomic960_castles: int = 1,
    directed_moves: int = 32,
    directed_captures: int = 23,
    directed_terminal_failures: int = 8,
    directed_promotions: int = 19,
    directed_en_passants: int = 6,
    directed_standard_castles: int = 4,
    directed_atomic960_castles: int = 11,
    directed_max_blast: int = 9,
    threads: int = 1,
    signature: str = "0123456789ABCDEF",
) -> str:
    actual = requested if actual is None else actual
    makes = actual // 2 if makes is None else makes
    undos = actual - makes if undos is None else undos
    evaluations = actual + 8 if evaluations is None else evaluations
    full_refresh = evaluations if full_refresh is None else full_refresh
    return (
        "AtomicNNUEV3 incremental stress gate passed: "
        f"mode={mode} requested-operations={requested} "
        f"actual-operations={actual} makes={makes} undos={undos} "
        f"evaluations={evaluations} "
        f"full-refresh-comparisons={full_refresh} "
        f"random-captures={captures} "
        f"random-terminal-failures={terminal_failures} "
        f"random-standard-castles={standard_castles} "
        f"random-atomic960-castles={atomic960_castles} "
        f"directed-moves={directed_moves} "
        f"directed-captures={directed_captures} "
        f"directed-terminal-failures={directed_terminal_failures} "
        f"directed-promotions={directed_promotions} "
        f"directed-en-passants={directed_en_passants} "
        f"directed-standard-castles={directed_standard_castles} "
        f"directed-atomic960-castles={directed_atomic960_castles} "
        f"directed-max-blast={directed_max_blast} threads={threads} "
        f"state-signature=0x{signature}"
    )


@pytest.mark.parametrize("mode", ("smoke", "release", "soak"))
def test_default_profiles_accept_their_frozen_or_pending_signature(
    mode: str,
) -> None:
    profile = WRAPPER.DEFAULT_PROFILES[mode]
    signature = profile.expected_signature or "0123456789ABCDEF"
    output = sentinel(
        mode=mode,
        requested=profile.operations,
        threads=profile.threads,
        signature=signature,
    )
    assert (
        WRAPPER.validate_gate_output(
            output,
            mode=mode,
            operations=profile.operations,
            full_refresh_interval=profile.full_refresh_interval,
            threads=profile.threads,
        )
        == signature
    )


def test_default_profile_signature_can_be_frozen_after_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = WRAPPER.DEFAULT_PROFILES["smoke"]
    monkeypatch.setitem(
        WRAPPER.DEFAULT_PROFILES,
        "smoke",
        WRAPPER.Profile(
            current.operations,
            current.full_refresh_interval,
            current.threads,
            current.timeout,
            "A1B2C3D4E5F60718",
        ),
    )
    accepted = sentinel(signature="A1B2C3D4E5F60718")
    assert (
        WRAPPER.validate_gate_output(
            accepted,
            mode="smoke",
            operations=4_096,
            full_refresh_interval=1,
            threads=1,
        )
        == "A1B2C3D4E5F60718"
    )
    assert (
        WRAPPER.validate_gate_output(
            sentinel(threads=4, signature="A1B2C3D4E5F60718"),
            mode="smoke",
            operations=4_096,
            full_refresh_interval=1,
            threads=4,
        )
        == "A1B2C3D4E5F60718"
    )
    with pytest.raises(WRAPPER.GateOutputError, match="signature mismatch"):
        WRAPPER.validate_gate_output(
            sentinel(signature="0000000000000001"),
            mode="smoke",
            operations=4_096,
            full_refresh_interval=1,
            threads=1,
        )


def test_zero_exit_unknown_command_is_not_a_pass() -> None:
    with pytest.raises(WRAPPER.GateOutputError, match="success sentinel"):
        WRAPPER.validate_gate_output(
            "Unknown command: --net\nUnknown command: --mode\n",
            mode="smoke",
            operations=4_096,
            full_refresh_interval=1,
            threads=1,
        )


def test_success_sentinel_must_be_the_final_non_empty_line() -> None:
    with pytest.raises(WRAPPER.GateOutputError, match="final non-empty line"):
        WRAPPER.validate_gate_output(
            sentinel() + "\ntrailing diagnostic\n",
            mode="smoke",
            operations=4_096,
            full_refresh_interval=1,
            threads=1,
        )


@pytest.mark.parametrize(
    ("output", "message"),
    (
        (sentinel(mode="release"), "mode=release"),
        (sentinel(requested=4_088), "operation count mismatch"),
        (sentinel(actual=4_088), "operation count mismatch"),
        (
            sentinel(makes=2_000, undos=2_000),
            "operation accounting mismatch",
        ),
        (sentinel(makes=2_047, undos=2_049), "did not unwind"),
        (sentinel(evaluations=4_096), "evaluation count mismatch"),
        (sentinel(full_refresh=4_096), "full-refresh accounting mismatch"),
        (sentinel(captures=2_049), "greater than random makes"),
        (
            sentinel(captures=2, terminal_failures=3),
            "random terminal failure accounting mismatch",
        ),
        (
            sentinel(standard_castles=2_048, atomic960_castles=1),
            "random castling accounting mismatch",
        ),
        (sentinel(directed_moves=31), "directed fixture accounting mismatch"),
        (sentinel(threads=2), "threads=2"),
        (
            sentinel(signature="ABCDEF"),
            "final non-empty line",
        ),
    ),
)
def test_mismatched_success_claims_are_rejected(output: str, message: str) -> None:
    with pytest.raises(WRAPPER.GateOutputError, match=message):
        WRAPPER.validate_gate_output(
            output,
            mode="smoke",
            operations=4_096,
            full_refresh_interval=1,
            threads=1,
        )


def test_override_profile_still_requires_coherent_accounting() -> None:
    output = sentinel(
        requested=80,
        captures=20,
        terminal_failures=1,
        standard_castles=1,
        atomic960_castles=0,
        full_refresh=24,
        threads=4,
    )
    assert (
        WRAPPER.validate_gate_output(
            output,
            mode="smoke",
            operations=80,
            full_refresh_interval=7,
            threads=4,
        )
        == "0123456789ABCDEF"
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"operations": 0}, "operations must be positive"),
        ({"threads": 3}, "threads must be one of"),
        ({"operations": 24, "threads": 4}, "multiple of 16"),
        ({"full_refresh_interval": 0}, "interval must be positive"),
        (
            {"operations": 80, "full_refresh_interval": 81},
            "cannot exceed",
        ),
        ({"timeout": 0.0}, "timeout must be finite and positive"),
        ({"timeout": float("inf")}, "timeout must be finite and positive"),
    ),
)
def test_configuration_rejects_invalid_overrides(
    overrides: Dict[str, Any], message: str
) -> None:
    values: Dict[str, Any] = {
        "mode": "smoke",
        "operations": None,
        "full_refresh_interval": None,
        "threads": None,
        "timeout": None,
    }
    values.update(overrides)
    with pytest.raises(ValueError, match=message):
        WRAPPER.resolve_configuration(**values)


def test_fixture_authentication_checks_size_and_sha256_from_one_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fixture = tmp_path / "atomic-v3.nnue"
    payload = b"frozen-v3-test-fixture"
    fixture.write_bytes(payload)
    monkeypatch.setattr(WRAPPER, "FROZEN_NET_BYTES", len(payload))
    monkeypatch.setattr(WRAPPER, "FROZEN_NET_SHA256", hashlib.sha256(payload).hexdigest())
    assert WRAPPER.authenticate_fixture(fixture) == hashlib.sha256(payload).hexdigest()

    monkeypatch.setattr(WRAPPER, "FROZEN_NET_BYTES", len(payload) + 1)
    with pytest.raises(WRAPPER.FixtureAuthenticationError, match="size mismatch"):
        WRAPPER.authenticate_fixture(fixture)

    monkeypatch.setattr(WRAPPER, "FROZEN_NET_BYTES", len(payload))
    monkeypatch.setattr(WRAPPER, "FROZEN_NET_SHA256", "0" * 64)
    with pytest.raises(WRAPPER.FixtureAuthenticationError, match="SHA-256 mismatch"):
        WRAPPER.authenticate_fixture(fixture)


def test_main_passes_one_explicit_reproducible_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    binary = tmp_path / "stress-runner"
    net = tmp_path / "atomic-v3.nnue"
    binary.write_bytes(b"binary")
    net.write_bytes(b"fixture")
    args = SimpleNamespace(
        binary=binary,
        net=net,
        mode="smoke",
        operations=80,
        full_refresh_interval=7,
        threads=4,
        timeout=12.5,
    )
    observed: Dict[str, Any] = {}

    monkeypatch.setattr(WRAPPER, "parse_args", lambda: args)
    monkeypatch.setattr(
        WRAPPER, "authenticate_fixture", lambda _path: WRAPPER.FROZEN_NET_SHA256
    )

    def fake_run(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            sentinel(
                requested=80,
                captures=20,
                terminal_failures=1,
                standard_castles=1,
                atomic960_castles=0,
                full_refresh=24,
                threads=4,
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert WRAPPER.main() == 0
    assert observed["command"] == [
        str(binary.resolve()),
        "--net",
        str(net.resolve()),
        "--mode",
        "smoke",
        "--operations",
        "80",
        "--full-refresh-interval",
        "7",
        "--threads",
        "4",
    ]
    assert observed["kwargs"]["timeout"] == 12.5
    assert observed["kwargs"]["check"] is False
    assert "signature=measurement-pending" in capsys.readouterr().out


def test_main_rejects_wrong_zero_exit_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    binary = tmp_path / "wrong-binary"
    net = tmp_path / "atomic-v3.nnue"
    binary.write_bytes(b"binary")
    net.write_bytes(b"fixture")
    args = SimpleNamespace(
        binary=binary,
        net=net,
        mode="smoke",
        operations=None,
        full_refresh_interval=None,
        threads=None,
        timeout=1.0,
    )
    monkeypatch.setattr(WRAPPER, "parse_args", lambda: args)
    monkeypatch.setattr(
        WRAPPER, "authenticate_fixture", lambda _path: WRAPPER.FROZEN_NET_SHA256
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Unknown command: --net\n"
        ),
    )
    assert WRAPPER.main() == 1
    assert "output validation failed" in capsys.readouterr().err


def test_wrapper_is_python39_and_signatures_are_explicit() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    ast.parse(source, filename=str(MODULE_PATH), feature_version=9)
    ast.parse(
        Path(__file__).read_text(encoding="utf-8"),
        filename=__file__,
        feature_version=9,
    )
    assert WRAPPER.FROZEN_NET_BYTES == 77_349_879
    assert WRAPPER.FROZEN_NET_SHA256 == (
        "00e46223822d06d7927e884eec10739ba19ef8dd82a6e262f627d361658080c2"
    )
    assert WRAPPER.DIRECTED_COUNTS == {
        "moves": 32,
        "captures": 23,
        "terminal_failures": 8,
        "promotions": 19,
        "en_passants": 6,
        "standard_castles": 4,
        "atomic960_castles": 11,
        "max_blast": 9,
    }
    assert WRAPPER.DEFAULT_PROFILES["smoke"].expected_signature == "45D43FB02CAA9A3D"
    assert WRAPPER.DEFAULT_PROFILES["release"].expected_signature == "E86C39BDF8187078"
    assert WRAPPER.DEFAULT_PROFILES["soak"].expected_signature == "AF6B51180815972B"
