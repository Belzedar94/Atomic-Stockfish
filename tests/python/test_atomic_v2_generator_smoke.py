from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests" / "atomic_v2_generator_smoke.py"
SPEC = importlib.util.spec_from_file_location("atomic_v2_generator_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


def write_inputs(tmp_path: Path) -> tuple[Path, Path, str]:
    generator = tmp_path / "atomic-stockfish-data-generator"
    net = tmp_path / "controlled.nnue"
    generator.write_bytes(b"not invoked directly in unit tests")
    net.write_bytes(b"AtomicNNUEV2 fixture")
    return generator, net, hashlib.sha256(net.read_bytes()).hexdigest()


def valid_manifest(net_sha256: str, *, records: object = "1") -> dict[str, object]:
    return {
        "network": {"file": "controlled.nnue", "sha256": net_sha256},
        "generation": {"use_nnue": "pure"},
        "statistics": {"records": records},
    }


def test_generator_commands_are_minimal_deterministic_pure_v2(tmp_path: Path) -> None:
    net = (tmp_path / "path with spaces" / "controlled.nnue").resolve()
    commands = SMOKE.generator_commands(net, "smoke.atbin")
    assert commands[0] == "uci"
    assert commands[-1] == "quit"
    assert f"setoption name EvalFile value {net}" in commands
    assert "setoption name Use NNUE value pure" in commands
    generation = next(line for line in commands if line.startswith("generate_training_data"))
    for field in (
        "depth 1",
        "count 1",
        "random_move_count 0",
        "output_file_name smoke.atbin",
        "data_format atomic-bin-v2",
        f"seed {SMOKE.DETERMINISTIC_SEED}",
    ):
        assert field in generation

    with pytest.raises(SMOKE.SmokeError, match="portable basename"):
        SMOKE.generator_commands(net, "nested/smoke.atbin")


def test_markers_require_v2_load_then_exact_single_finalization() -> None:
    SMOKE.validate_markers(
        f"{SMOKE.NETWORK_MARKER} controlled.nnue (version 0xA70C0002)\n"
        f"{SMOKE.FINAL_MARKER}\nINFO: records=1 draws=1\n"
    )
    with pytest.raises(SMOKE.SmokeError, match="load marker"):
        SMOKE.validate_markers(SMOKE.FINAL_MARKER)
    with pytest.raises(SMOKE.SmokeError, match="exactly one"):
        SMOKE.validate_markers(SMOKE.NETWORK_MARKER)
    with pytest.raises(SMOKE.SmokeError, match="finalized before"):
        SMOKE.validate_markers(f"{SMOKE.FINAL_MARKER}\n{SMOKE.NETWORK_MARKER}\n")
    with pytest.raises(SMOKE.SmokeError, match="one-record summary"):
        SMOKE.validate_markers(
            f"{SMOKE.NETWORK_MARKER}\n{SMOKE.FINAL_MARKER}\nlate diagnostic\n"
        )


def test_marker_validator_can_gate_the_public_v3_generator_backend() -> None:
    marker = "info string NNUE evaluation using AtomicNNUEV3"
    SMOKE.validate_markers(
        f"{marker} controlled.nnue (version 0xA70C0003)\n"
        f"{SMOKE.FINAL_MARKER}\nINFO: records=1 draws=0\n",
        backend="AtomicNNUEV3",
    )
    with pytest.raises(SMOKE.SmokeError, match="AtomicNNUEV3 load marker"):
        SMOKE.validate_markers(
            f"{SMOKE.NETWORK_MARKER}\n{SMOKE.FINAL_MARKER}\n"
            "INFO: records=1 draws=0\n",
            backend="AtomicNNUEV3",
        )


@pytest.mark.parametrize("value", (1, "1"))
def test_manifest_record_count_accepts_wire_and_integer_types(value: object) -> None:
    assert SMOKE._manifest_record_count(value) == 1


@pytest.mark.parametrize("value", (True, 1.0, "01", "one", None))
def test_manifest_record_count_rejects_ambiguous_types(value: object) -> None:
    with pytest.raises(SMOKE.SmokeError, match="statistics.records"):
        SMOKE._manifest_record_count(value)


def test_artifact_validation_checks_size_provenance_mode_and_count(
    tmp_path: Path,
) -> None:
    output = tmp_path / "smoke.atbin"
    manifest = tmp_path / "smoke.atbin.manifest.json"
    digest = "a" * 64
    output.write_bytes(bytes(SMOKE.EXPECTED_DATASET_SIZE))
    manifest.write_text(json.dumps(valid_manifest(digest)), encoding="utf-8")
    SMOKE.validate_artifacts(output, manifest, expected_net_sha256=digest)

    cases = (
        ({"network": {"sha256": "b" * 64}}, "network.sha256"),
        ({"generation": {"use_nnue": "true"}}, "generation.use_nnue"),
        ({"statistics": {"records": "2"}}, "statistics.records"),
    )
    for replacement, message in cases:
        value = valid_manifest(digest)
        value.update(replacement)
        manifest.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(SMOKE.SmokeError, match=re.escape(message)):
            SMOKE.validate_artifacts(output, manifest, expected_net_sha256=digest)

    output.write_bytes(bytes(SMOKE.EXPECTED_DATASET_SIZE - 1))
    with pytest.raises(SMOKE.SmokeError, match="framing mismatch"):
        SMOKE.validate_artifacts(output, manifest, expected_net_sha256=digest)


def test_run_smoke_uses_private_cwd_and_never_overwrites(tmp_path: Path) -> None:
    generator, net, digest = write_inputs(tmp_path)
    observed: dict[str, object] = {}

    def fake_runner(args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        cwd = Path(kwargs["cwd"])
        command_stream = kwargs["input"]
        generation = next(
            line
            for line in command_stream.splitlines()
            if line.startswith("generate_training_data")
        )
        output_name = re.search(r"output_file_name (\S+)", generation).group(1)
        output = cwd / output_name
        manifest = Path(f"{output}.manifest.json")
        assert not output.exists()
        assert not manifest.exists()
        output.write_bytes(bytes(SMOKE.EXPECTED_DATASET_SIZE))
        manifest.write_text(json.dumps(valid_manifest(digest)), encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                f"{SMOKE.NETWORK_MARKER} {net}\n{SMOKE.FINAL_MARKER}\n"
                "INFO: records=1 draws=1\n"
            ),
            stderr="",
        )

    sentinel = SMOKE.run_smoke(generator, net, digest.upper(), runner=fake_runner)
    assert "records=1 bytes=160" in sentinel
    assert observed["args"] == [str(generator.resolve())]
    kwargs = observed["kwargs"]
    assert kwargs["check"] is False
    assert kwargs["text"] is True
    assert "shell" not in kwargs
    assert Path(kwargs["cwd"]).name.startswith("atomic-v2-generator-smoke-")


def test_preflight_rejects_existing_output_paths(tmp_path: Path) -> None:
    output = tmp_path / "smoke.atbin"
    manifest = Path(f"{output}.manifest.json")
    output.write_bytes(b"user data")
    with pytest.raises(SMOKE.SmokeError, match="refusing to overwrite"):
        SMOKE._require_output_paths_absent(output, manifest)
    assert output.read_bytes() == b"user data"


def test_wrong_network_hash_fails_before_launch(tmp_path: Path) -> None:
    generator, net, _digest = write_inputs(tmp_path)

    def forbidden_runner(*_args, **_kwargs):
        raise AssertionError("runner must not be called")

    with pytest.raises(SMOKE.SmokeError, match="before launch"):
        SMOKE.run_smoke(generator, net, "0" * 64, runner=forbidden_runner)


def test_subprocess_timeout_and_nonzero_exit_are_clear(tmp_path: Path) -> None:
    generator, _net, _digest = write_inputs(tmp_path)

    def timeout_runner(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("generator", 0.01, output="partial stdout")

    with pytest.raises(SMOKE.SmokeError, match=r"timed out[\s\S]*Partial output"):
        SMOKE.run_generator(
            generator,
            ("uci", "quit"),
            cwd=tmp_path,
            timeout=0.01,
            runner=timeout_runner,
        )

    def failing_runner(args, **_kwargs):
        return subprocess.CompletedProcess(args, 7, "stdout marker", "stderr marker")

    with pytest.raises(SMOKE.SmokeError, match=r"exit code 7[\s\S]*stdout marker"):
        SMOKE.run_generator(
            generator,
            ("uci", "quit"),
            cwd=tmp_path,
            timeout=1.0,
            runner=failing_runner,
        )
