from __future__ import annotations

from contextlib import nullcontext
import hashlib
import inspect
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
from typing import Callable

import pytest


TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import legacy_pipeline_e2e as pipeline
from legacy_pipeline_lock import EXPECTED_BUILD_RECIPES, PipelineProfile


class FakeDistribution:
    def __init__(self, root: Path, name: str, version: str, files: list[Path]) -> None:
        self.root = root
        self.name = name
        self.version = version
        self.files = tuple(files)

    def locate_file(self, entry: Path) -> Path:
        return self.root / entry


@pytest.mark.parametrize("measurement", (False, True))
def test_e2e_checkout_verification_forwards_measurement_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    measurement: bool,
) -> None:
    observed: list[bool] = []

    def verify(lock: object, **arguments: object) -> dict[str, object]:
        del lock
        observed.append(bool(arguments.pop("allow_unresolved_hashes")))
        assert arguments == {
            "tools_root": tmp_path / "tools",
            "trainer_root": tmp_path / "trainer",
            "atomic_root": tmp_path / "atomic",
            "tools_engine": tmp_path / "tools" / "stockfish",
            "engine": tmp_path / "atomic" / "atomic-stockfish",
            "atomic_commit": "a" * 40,
        }
        return {}

    monkeypatch.setattr(pipeline, "verify_release_checkouts", verify)
    pipeline.verify_pipeline_checkouts(
        object(),
        tools_root=tmp_path / "tools",
        trainer_root=tmp_path / "trainer",
        atomic_root=tmp_path / "atomic",
        tools_engine=tmp_path / "tools" / "stockfish",
        engine=tmp_path / "atomic" / "atomic-stockfish",
        atomic_commit="a" * 40,
        measure_synthetic_fixture=measurement,
    )

    assert observed == [measurement]


def fake_python_environment(
    tmp_path: Path,
) -> tuple[
    dict[str, SimpleNamespace],
    dict[str, SimpleNamespace],
    Callable[[str], FakeDistribution],
]:
    modules: dict[str, SimpleNamespace] = {}
    module_table: dict[str, SimpleNamespace] = {}
    distributions: dict[str, FakeDistribution] = {}
    for module_name, distribution_name in pipeline.PYTHON_DEPENDENCIES:
        package = tmp_path / module_name
        package.mkdir()
        origin = package / "__init__.py"
        origin.write_text(f"__version__ = '1.2.3'\n", encoding="utf-8")
        dist_info = tmp_path / (
            distribution_name.replace("-", "_") + "-1.2.3.dist-info"
        )
        dist_info.mkdir()
        metadata = dist_info / "METADATA"
        record = dist_info / "RECORD"
        metadata.write_text(
            f"Name: {distribution_name}\nVersion: 1.2.3\n", encoding="utf-8"
        )
        record.write_text("installed fixture\n", encoding="utf-8")
        files = [
            origin.relative_to(tmp_path),
            metadata.relative_to(tmp_path),
            record.relative_to(tmp_path),
        ]
        module = SimpleNamespace(__file__=str(origin), __version__="1.2.3")
        modules[module_name] = module
        module_table[module_name] = module
        distributions[distribution_name] = FakeDistribution(
            tmp_path, distribution_name, "1.2.3", files
        )

    def resolve(name: str) -> FakeDistribution:
        return distributions[name]

    return modules, module_table, resolve


def profile(**overrides: object) -> PipelineProfile:
    values: dict[str, object] = {
        "name": "synthetic-ci",
        "source_kind": "trainer-generated-zero",
        "records": 8,
        "seed": "20260711",
        "source_net_sha256": "0" * 64,
        "data_sha256": "1" * 64,
        "hashes_resolved": True,
        "build_recipes": EXPECTED_BUILD_RECIPES["synthetic-ci"],
        "synthetic_model_seed": 20260711,
    }
    values.update(overrides)
    return PipelineProfile(**values)


def schema_lock() -> SimpleNamespace:
    return SimpleNamespace(
        training_data_schema=SimpleNamespace(
            schema_id="legacy-atomic-v1",
            sha256=(
                "acca0f551f1c012c31a6c727dedccaebb7b5ebbc46810edb87e31bb208d5abe1"
            ),
            record_size=72,
        )
    )


def capability(*, write: bool, sha256: str | None = None) -> dict[str, object]:
    return {
        "schema_sha256": sha256 or schema_lock().training_data_schema.sha256,
        "formats": {
            "legacy-atomic-v1": {
                "read": True,
                "write": write,
                "record_size": 72,
            }
        },
    }


def test_training_data_schema_handshake_is_fail_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tools_payload = json.dumps(capability(write=True), separators=(",", ":"))
    monkeypatch.setattr(
        pipeline,
        "run_tools_command",
        lambda engine, command: (
            f"id name fixture\nreadyok\n{tools_payload}\n"
            if engine == tmp_path / "tools" and command == "atomic_data_schema"
            else ""
        ),
    )
    trainer = SimpleNamespace(
        atomic_training_data_schema=lambda: capability(write=False)
    )
    pipeline.verify_training_data_schema_handshake(
        schema_lock(),
        tools_engine=tmp_path / "tools",
        nnue_dataset=trainer,
    )
    assert "LEGACY PIPELINE SCHEMA VERIFIED" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("target", "value", "message"),
    (
        ("tools-hash", "f" * 64, "tools schema SHA-256 mismatch"),
        ("trainer-hash", "f" * 64, "trainer schema SHA-256 mismatch"),
        ("tools-write", False, "read=true, write=true"),
        ("trainer-write", True, "read=true, write=false"),
        ("trainer-size", 71, "trainer record size mismatch"),
    ),
)
def test_training_data_schema_handshake_rejects_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    value: object,
    message: str,
) -> None:
    tools = capability(write=True)
    trainer = capability(write=False)
    selected = tools if target.startswith("tools") else trainer
    if target.endswith("hash"):
        selected["schema_sha256"] = value
    elif target.endswith("write"):
        selected["formats"]["legacy-atomic-v1"]["write"] = value
    else:
        selected["formats"]["legacy-atomic-v1"]["record_size"] = value
    monkeypatch.setattr(
        pipeline,
        "run_tools_command",
        lambda unused_engine, unused_command: (
            json.dumps(tools, separators=(",", ":")) + "\nreadyok\n"
        ),
    )
    nnue_dataset = SimpleNamespace(
        atomic_training_data_schema=lambda: trainer
    )
    with pytest.raises(AssertionError, match=message):
        pipeline.verify_training_data_schema_handshake(
            schema_lock(),
            tools_engine=tmp_path / "tools",
            nnue_dataset=nnue_dataset,
        )


def test_tools_schema_response_rejects_duplicate_json_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.dumps(capability(write=True), separators=(",", ":"))
    payload = payload.replace(
        '"schema_sha256":', '"schema_sha256":"0","schema_sha256":', 1
    )
    monkeypatch.setattr(
        pipeline,
        "run_tools_command",
        lambda unused_engine, unused_command: payload + "\nreadyok\n",
    )
    with pytest.raises(AssertionError, match="duplicate JSON key"):
        pipeline.query_tools_training_data_schema(tmp_path / "tools")


def test_profile_arguments_are_locked() -> None:
    args = SimpleNamespace(records=None, seed=None, source_net=None)
    assert pipeline.resolve_profile_arguments(args, profile()) == (8, "20260711")

    args.records = 7
    with pytest.raises(AssertionError, match="does not match locked"):
        pipeline.resolve_profile_arguments(args, profile())

    args.records = None
    args.source_net = Path("strong.nnue")
    with pytest.raises(AssertionError, match="omit --source-net"):
        pipeline.resolve_profile_arguments(args, profile())


def test_strong_profile_requires_external_source() -> None:
    strong = profile(
        name="strong-local",
        source_kind="external",
        synthetic_model_seed=None,
    )
    args = SimpleNamespace(records=None, seed=None, source_net=None)
    with pytest.raises(AssertionError, match="requires --source-net"):
        pipeline.resolve_profile_arguments(args, strong)


def atomic_generator_marker(source_hash: str, data_hash: str) -> str:
    return (
        "Atomic data-generator tests passed fixtures=7 "
        f"data_sha256={data_hash.upper()} net_sha256={source_hash.upper()}"
    )


def test_generate_data_uses_the_atomic_pure_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "generated.bin"
    observed: dict[str, str] = {}

    def fake_run(
        engine: Path,
        source_net: Path,
        command: str,
        *,
        pure_option: str,
        pure_load_suffix: str,
    ) -> str:
        del engine, source_net, command
        observed.update(option=pure_option, suffix=pure_load_suffix)
        output.write_bytes(b"target-specific-protocol")
        return ""

    monkeypatch.setattr(pipeline, "run_generation_command", fake_run)
    generated = pipeline.generate_data(
        tmp_path / "engine",
        tmp_path / "source.nnue",
        output,
        8,
        "seed",
    )
    assert generated == b"target-specific-protocol"
    assert observed == {
        "option": pipeline.ATOMIC_PURE_OPTION,
        "suffix": pipeline.ATOMIC_PURE_LOAD_SUFFIX,
    }


def test_legacy_nnue_load_marker_names_the_selected_backend() -> None:
    assert (
        pipeline.LEGACY_NNUE_LOAD_PREFIX
        == "info string NNUE evaluation using Legacy Atomic V1 "
    )
    source = inspect.getsource(pipeline)
    assert source.count("f\"{LEGACY_NNUE_LOAD_PREFIX}") == 2


@pytest.mark.parametrize(
    ("source_hash", "data_hash"),
    tuple(pipeline.ATOMIC_DATA_GENERATOR_BASIC_SHA256.items()),
)
def test_atomic_generator_marker_is_exact_and_source_pinned(
    source_hash: str, data_hash: str
) -> None:
    marker = atomic_generator_marker(source_hash, data_hash)
    assert pipeline.parse_atomic_data_generator_marker(
        f"diagnostic\n{marker}\n", source_net_hash=source_hash
    ) == data_hash


def test_atomic_generator_marker_rejects_duplicates_and_hash_drift() -> None:
    source_hash, data_hash = next(
        iter(pipeline.ATOMIC_DATA_GENERATOR_BASIC_SHA256.items())
    )
    marker = atomic_generator_marker(source_hash, data_hash)
    with pytest.raises(AssertionError, match="exactly one exact"):
        pipeline.parse_atomic_data_generator_marker(
            f"{marker}\n{marker}\n", source_net_hash=source_hash
        )
    with pytest.raises(AssertionError, match="network mismatch"):
        pipeline.parse_atomic_data_generator_marker(
            atomic_generator_marker("a" * 64, data_hash),
            source_net_hash=source_hash,
        )
    with pytest.raises(AssertionError, match="basic fixture changed"):
        pipeline.parse_atomic_data_generator_marker(
            atomic_generator_marker(source_hash, "b" * 64),
            source_net_hash=source_hash,
        )


def test_profile_pins_the_atomic_owned_dataset() -> None:
    selected = profile(data_sha256="a" * 64)
    pipeline.verify_profile_data_hash(selected, data_hash="a" * 64)
    with pytest.raises(AssertionError, match="Atomic generator fixture changed"):
        pipeline.verify_profile_data_hash(selected, data_hash="c" * 64)


def test_python_environment_provenance_is_auditable_and_rehashed(
    tmp_path: Path,
) -> None:
    modules, module_table, resolver = fake_python_environment(tmp_path)

    def capture() -> pipeline.PythonEnvironmentProvenance:
        return pipeline.capture_python_environment_provenance(
            dependency_modules=modules,
            module_table=module_table,
            distribution_resolver=resolver,
        )

    before = capture()
    output: list[str] = []
    pipeline.emit_python_environment_provenance(before, emit=output.append)
    assert any("implementation=cpython" in line for line in output)
    assert any(
        "module=torch" in line and "version=\"1.2.3\"" in line
        for line in output
    )
    assert any("origin=" in line and "manifest_sha256=" in line for line in output)

    torch_origin = Path(modules["torch"].__file__)
    torch_origin.write_text("__version__ = 'mutated'\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="Python artifact changed after preflight"):
        pipeline.verify_python_environment_provenance(
            before, emit=lambda unused: None, recapture=capture
        )


def test_python_environment_rejects_unowned_module_origin(tmp_path: Path) -> None:
    modules, module_table, resolver = fake_python_environment(tmp_path)
    rogue = tmp_path / "rogue" / "torch.py"
    rogue.parent.mkdir()
    rogue.write_text("__version__ = '1.2.3'\n", encoding="utf-8")
    modules["torch"].__file__ = str(rogue)
    module_table["torch"].__file__ = str(rogue)

    with pytest.raises(AssertionError, match="not owned by distribution torch"):
        pipeline.capture_python_environment_provenance(
            dependency_modules=modules,
            module_table=module_table,
            distribution_resolver=resolver,
        )


def test_python_environment_allows_owned_lazy_imports(tmp_path: Path) -> None:
    modules, module_table, resolver = fake_python_environment(tmp_path)

    def capture() -> pipeline.PythonEnvironmentProvenance:
        return pipeline.capture_python_environment_provenance(
            dependency_modules=modules,
            module_table=module_table,
            distribution_resolver=resolver,
        )

    lazy = tmp_path / "torch" / "lazy.py"
    lazy.write_text("VALUE = 1\n", encoding="utf-8")
    torch_distribution = resolver("torch")
    torch_distribution.files += (lazy.relative_to(tmp_path),)
    before = capture()
    module_table["torch.lazy"] = SimpleNamespace(__file__=str(lazy))

    output: list[str] = []
    pipeline.verify_python_environment_provenance(
        before, emit=output.append, recapture=capture
    )
    assert "lazy_added_files=1" in output[-1]


def test_python_environment_rejects_missing_dependency_version(
    tmp_path: Path,
) -> None:
    modules, module_table, resolver = fake_python_environment(tmp_path)
    del modules["numpy"].__version__

    with pytest.raises(AssertionError, match="numpy has no exact non-empty version"):
        pipeline.capture_python_environment_provenance(
            dependency_modules=modules,
            module_table=module_table,
            distribution_resolver=resolver,
        )


class FakeParameter:
    def __init__(self) -> None:
        self.value = 17

    def zero_(self) -> None:
        self.value = 0


class FakeNetwork:
    def __init__(self, feature_set: object, lambda_: float) -> None:
        assert lambda_ == 1.0
        self.feature_set = feature_set
        self.weights = [FakeParameter(), FakeParameter()]
        self.eval_called = False

    def parameters(self) -> list[FakeParameter]:
        return self.weights

    def eval(self) -> None:
        self.eval_called = True


class FakeWriter:
    def __init__(self, network: FakeNetwork, description: str) -> None:
        assert network.eval_called
        assert all(parameter.value == 0 for parameter in network.weights)
        self.buf = bytearray(
            struct.pack(
                "<II",
                pipeline.LEGACY_NNUE_VERSION,
                pipeline.LEGACY_NNUE_ARCHITECTURE,
            )
            + description.encode("utf-8")
        )


def test_synthetic_source_uses_trainer_model_and_zeroes_every_parameter(
    tmp_path: Path,
) -> None:
    serialized = struct.pack(
        "<II", pipeline.LEGACY_NNUE_VERSION, pipeline.LEGACY_NNUE_ARCHITECTURE
    ) + pipeline.SYNTHETIC_DESCRIPTION.encode("utf-8")
    expected_hash = hashlib.sha256(serialized).hexdigest()
    synthetic = profile(source_net_sha256=expected_hash)
    torch = SimpleNamespace(
        manual_seed=lambda seed: seed,
        no_grad=lambda: nullcontext(),
    )
    features = SimpleNamespace(
        get_feature_set_from_name=lambda name: {"name": name}
    )
    model = SimpleNamespace(NNUE=FakeNetwork)
    serialize = SimpleNamespace(NNUEWriter=FakeWriter)

    output, actual = pipeline.generate_synthetic_source_network(
        tmp_path, synthetic, features, model, serialize, torch
    )
    assert output.name == "atomic-synthetic-source.nnue"
    assert actual == serialized
    assert output.read_bytes() == serialized

    changed = profile(source_net_sha256="f" * 64)
    with pytest.raises(AssertionError, match="fixture changed"):
        pipeline.generate_synthetic_source_network(
            tmp_path, changed, features, model, serialize, torch
        )

    output, actual = pipeline.generate_synthetic_source_network(
        tmp_path,
        changed,
        features,
        model,
        serialize,
        torch,
        verify_hash=False,
    )
    assert output.read_bytes() == actual == serialized
