from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tests" / "atomic_nnue_incremental.py"
SPEC = importlib.util.spec_from_file_location("atomic_nnue_incremental_wrapper", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
WRAPPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WRAPPER)


def sentinel(
    *,
    mode: str = "smoke",
    requested: int = 4_096,
    actual: int | None = None,
    makes: int | None = None,
    undos: int | None = None,
    forced_refresh: int = 0,
    signature: str = "DDB8196C6A0BE4A8",
) -> str:
    actual = requested if actual is None else actual
    makes = actual // 2 if makes is None else makes
    undos = actual - makes if undos is None else undos
    return (
        "LegacyAtomicV1 incremental gate passed: "
        f"mode={mode} requested-random-operations={requested} "
        f"actual-random-operations={actual} makes={makes} undos={undos} "
        "captures=120 "
        f"capture-forced-refresh={forced_refresh} "
        "perspective-refresh-white=206 perspective-refresh-black=542 "
        "full-refresh-comparisons=4104 "
        f"state-signature=0x{signature}"
    )


@pytest.mark.parametrize(
    ("mode", "operations", "interval", "signature"),
    (
        ("smoke", 4_096, 1, "DDB8196C6A0BE4A8"),
        ("release", 1_000_000, 1_024, "8742E39B793C46AB"),
    ),
)
def test_default_profiles_require_the_frozen_signature(
    mode: str, operations: int, interval: int, signature: str
):
    output = sentinel(mode=mode, requested=operations, signature=signature)
    assert (
        WRAPPER.validate_gate_output(
            output,
            mode=mode,
            operations=operations,
            full_refresh_interval=interval,
        )
        == signature
    )


def test_zero_exit_unknown_command_is_not_a_pass():
    with pytest.raises(WRAPPER.GateOutputError, match="success sentinel"):
        WRAPPER.validate_gate_output(
            "Unknown command: --net\nUnknown command: --mode\n",
            mode="smoke",
            operations=4_096,
            full_refresh_interval=1,
        )


def test_success_sentinel_must_be_the_final_non_empty_line():
    with pytest.raises(WRAPPER.GateOutputError, match="final non-empty line"):
        WRAPPER.validate_gate_output(
            sentinel() + "\ntrailing diagnostic\n",
            mode="smoke",
            operations=4_096,
            full_refresh_interval=1,
        )


@pytest.mark.parametrize(
    ("output", "mode", "operations", "message"),
    (
        (sentinel(mode="release"), "smoke", 4_096, "mode=release"),
        (sentinel(requested=4_088), "smoke", 4_096, "operation count mismatch"),
        (
            sentinel(actual=4_088),
            "smoke",
            4_096,
            "operation count mismatch",
        ),
        (
            sentinel(makes=2_000, undos=2_000),
            "smoke",
            4_096,
            "operation accounting mismatch",
        ),
        (
            sentinel(forced_refresh=1),
            "smoke",
            4_096,
            "capture-forced-refresh=1",
        ),
        (
            sentinel(signature="0000000000000001"),
            "smoke",
            4_096,
            "signature mismatch",
        ),
    ),
)
def test_mismatched_success_claims_are_rejected(
    output: str, mode: str, operations: int, message: str
):
    with pytest.raises(WRAPPER.GateOutputError, match=message):
        WRAPPER.validate_gate_output(
            output,
            mode=mode,
            operations=operations,
            full_refresh_interval=1,
        )


def test_override_profile_still_requires_a_coherent_sentinel():
    signature = "0123456789ABCDEF"
    output = sentinel(requested=80, makes=40, undos=40, signature=signature)
    assert (
        WRAPPER.validate_gate_output(
            output,
            mode="smoke",
            operations=80,
            full_refresh_interval=7,
        )
        == signature
    )


def test_main_rejects_wrong_zero_exit_executable(monkeypatch, tmp_path, capsys):
    binary = tmp_path / "wrong-binary"
    net = tmp_path / "frozen.nnue"
    binary.write_bytes(b"binary")
    net.write_bytes(b"network")
    args = SimpleNamespace(
        binary=binary,
        net=net,
        mode="smoke",
        operations=None,
        full_refresh_interval=None,
        timeout=1.0,
    )

    monkeypatch.setattr(WRAPPER, "parse_args", lambda: args)
    monkeypatch.setattr(WRAPPER, "sha256", lambda _path: WRAPPER.FROZEN_NET_SHA256)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Unknown command: --net\n"
        ),
    )

    assert WRAPPER.main() == 1
    assert "output validation failed" in capsys.readouterr().err
