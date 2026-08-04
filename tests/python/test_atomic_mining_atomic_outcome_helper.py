from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.atomic_mining import atomic_outcome_helper as helper
from tools.atomic_mining import build_atomic_outcome_binding as binding_builder


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


@pytest.fixture(scope="session")
def native_binding(
    tmp_path_factory: pytest.TempPathFactory,
) -> binding_builder.NativeBindingBuild:
    output = tmp_path_factory.mktemp("atomic-outcome-parent") / "fresh-binding"
    return binding_builder.build_native_rules_binding(
        REPO_ROOT,
        output,
        expected_source_commit=subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=5.0,
        ).stdout.strip(),
        timeout_seconds=180.0,
    )


def _native_outcome(
    native_binding: binding_builder.NativeBindingBuild,
    fen: str,
    moves: tuple[str, ...],
    *,
    claim_draw: bool = True,
) -> helper.AtomicOutcome:
    response = helper.invoke_atomic_outcome_helper(
        pyffish_path=native_binding.binding_path,
        pyffish_sha256=native_binding.binding_sha256,
        build_manifest_path=native_binding.manifest_path,
        build_manifest_sha256=native_binding.manifest_sha256,
        source_root=REPO_ROOT,
        source_commit=native_binding.source_commit,
        root_fen=fen,
        moves=moves,
        claim_draw=claim_draw,
        timeout_seconds=5.0,
    )
    outcome = response["outcome"]
    assert isinstance(outcome, dict)
    return helper.AtomicOutcome(
        terminal=outcome["terminal"],  # type: ignore[arg-type]
        termination=outcome["termination"],  # type: ignore[arg-type]
        result_white=outcome["result_white"],  # type: ignore[arg-type]
        value_side_to_move=outcome["value_side_to_move"],  # type: ignore[arg-type]
        final_fen=outcome["final_fen"],  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("name", "fen", "moves", "termination", "result"),
    (
        (
            "rook-is-sufficient",
            "7k/8/8/8/8/8/8/KR6 w - - 0 1",
            (),
            "ongoing",
            "*",
        ),
        (
            "rule50-99-is-not-a-current-draw",
            "r6k/8/8/8/8/8/8/R6K w - - 99 1",
            (),
            "ongoing",
            "*",
        ),
        (
            "rule50-100",
            "r6k/8/8/8/8/8/8/R6K w - - 100 1",
            (),
            "fifty-move-rule",
            "1/2-1/2",
        ),
        (
            "prospective-repetition-is-not-current",
            START,
            ("g1f3", "b8c6", "f3g1", "c6b8", "g1f3", "b8c6", "f3g1"),
            "ongoing",
            "*",
        ),
        (
            "current-threefold-repetition",
            START,
            (
                "g1f3",
                "b8c6",
                "f3g1",
                "c6b8",
                "g1f3",
                "b8c6",
                "f3g1",
                "c6b8",
            ),
            "threefold-repetition",
            "1/2-1/2",
        ),
        (
            "atomic-explosion",
            START,
            ("e2e4", "e7e5", "d1h5", "a7a6", "h5f7"),
            "atomic-explosion",
            "1-0",
        ),
        (
            "checkmate",
            "BQ6/Rk6/8/8/8/8/8/4K3 b - - 0 1",
            (),
            "checkmate",
            "1-0",
        ),
        (
            "stalemate",
            "KQ6/Rk6/2B5/8/8/8/8/8 b - - 0 1",
            (),
            "stalemate",
            "1/2-1/2",
        ),
        (
            "insufficient-material",
            "8/8/8/8/3K4/3k4/8/8 b - - 0 1",
            (),
            "insufficient-material",
            "1/2-1/2",
        ),
    ),
)
def test_native_golden_outcomes(
    native_binding: binding_builder.NativeBindingBuild,
    name: str,
    fen: str,
    moves: tuple[str, ...],
    termination: str,
    result: str,
) -> None:
    del name
    outcome = _native_outcome(native_binding, fen, moves)
    assert outcome.termination == termination
    assert outcome.result_white == result
    assert outcome.terminal == (termination != "ongoing")


def test_optional_draw_policy_can_be_disabled(
    native_binding: binding_builder.NativeBindingBuild,
) -> None:
    outcome = _native_outcome(
        native_binding,
        "r6k/8/8/8/8/8/8/R6K w - - 100 1",
        (),
        claim_draw=False,
    )
    assert outcome.termination == "ongoing"


def _optional_native(value: object) -> ModuleType:
    native = ModuleType("strict_optional_native")
    native.FEN_OK = 1
    native.validate_fen = lambda *_args: 1
    native.get_fen = lambda *_args: START
    native.is_immediate_game_end = lambda *_args: (False, 0)
    native.has_insufficient_material = lambda *_args: (False, False)
    native.legal_moves = lambda *_args: ["e2e4"]
    native.is_optional_game_end = lambda *_args: value
    return native


def test_optional_terminal_accepts_exact_two_item_list() -> None:
    outcome = helper.evaluate_atomic_outcome(
        _optional_native([False, 0]), START, ()
    )
    assert outcome.terminal is False
    assert outcome.termination == "ongoing"


@pytest.mark.parametrize(
    "value",
    (
        (False,),
        (False, 0, 0),
        (0, 0),
        (False, False),
        (False, 0.0),
        (False, 1),
        "not-a-pair",
    ),
)
def test_optional_terminal_rejects_non_exact_shape(value: object) -> None:
    with pytest.raises(
        helper.AtomicOutcomeProtocolError,
        match="optional outcome is malformed|wrong type",
    ):
        helper.evaluate_atomic_outcome(_optional_native(value), START, ())


def test_request_wire_is_canonical_and_strict() -> None:
    request = {
        "claim_draw": True,
        "moves": [],
        "root_fen": START,
        "schema": helper.REQUEST_SCHEMA,
    }
    canonical = (
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )
    assert helper.parse_request_bytes(canonical) == request
    with pytest.raises(helper.AtomicOutcomeProtocolError, match="canonical"):
        helper.parse_request_bytes(json.dumps(request).encode() + b"\n")
    with pytest.raises(helper.AtomicOutcomeProtocolError, match="one LF"):
        helper.parse_request_bytes(canonical + b"\n")


def test_subprocess_client_is_deadline_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    def timed_out(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise subprocess.TimeoutExpired(["helper"], 0.01)

    monkeypatch.setattr(subprocess, "run", timed_out)
    with pytest.raises(helper.AtomicOutcomeTimeout):
        helper.invoke_atomic_outcome_helper(
            pyffish_path=Path("unused-pyffish"),
            pyffish_sha256="1" * 64,
            build_manifest_path=Path("unused-manifest"),
            build_manifest_sha256="2" * 64,
            source_root=REPO_ROOT,
            source_commit="0" * 40,
            root_fen=START,
            moves=(),
            timeout_seconds=0.01,
        )


def test_exact_binding_hash_is_mandatory(
    native_binding: binding_builder.NativeBindingBuild,
) -> None:
    with pytest.raises(helper.AtomicOutcomeProvenanceError, match="differs"):
        helper._load_native_binding(native_binding.binding_path, "0" * 64)


def test_ambient_pyffish_is_rejected_before_path_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "pyffish", ModuleType("pyffish"))
    with pytest.raises(
        helper.AtomicOutcomeProvenanceError, match="ambient pyffish"
    ):
        helper._load_native_binding(Path("does-not-exist"), "0" * 64)


def test_exact_build_manifest_hash_is_mandatory(
    native_binding: binding_builder.NativeBindingBuild,
) -> None:
    with pytest.raises(helper.AtomicOutcomeProvenanceError, match="differs"):
        helper._load_build_manifest(
            native_binding.manifest_path,
            "0" * 64,
            native_binding={
                "bytes": native_binding.binding_path.stat().st_size,
                "path": str(native_binding.binding_path),
                "sha256": native_binding.binding_sha256,
            },
            source_root=REPO_ROOT,
            source_commit=native_binding.source_commit,
        )


def test_fresh_build_manifest_binds_sources_runtime_and_toolchain(
    native_binding: binding_builder.NativeBindingBuild,
) -> None:
    manifest_wire = native_binding.manifest_path.read_bytes()
    assert hashlib.sha256(manifest_wire).hexdigest() == native_binding.manifest_sha256
    assert manifest_wire.endswith(b"\n")
    manifest = json.loads(manifest_wire)
    assert manifest["schema"] == binding_builder.MANIFEST_SCHEMA
    assert manifest["binding"]["sha256"] == native_binding.binding_sha256
    assert manifest["source"]["commit"] == native_binding.source_commit
    assert len(manifest["source"]["files"]) >= 100
    assert manifest["toolchain"]
    assert manifest["python"]["sha256"]
    assert manifest["python"]["runtime_libraries"]
    assert manifest["build"]["returncode"] == 0


def _synthetic_build_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, object], dict[str, object], Path, Path]:
    binding = tmp_path / "original-pyffish.pyd"
    binding.write_bytes(b"binding")
    loaded = tmp_path / "snapshot-pyffish.pyd"
    loaded.write_bytes(binding.read_bytes())
    stdout = tmp_path / "build.stdout.log"
    stderr = tmp_path / "build.stderr.log"
    stdout.write_bytes(b"compiler output\n")
    stderr.write_bytes(b"")
    cl = tmp_path / "cl.exe"
    link = tmp_path / "link.exe"
    cl.write_bytes(b"cl")
    link.write_bytes(b"link")
    source_files = {"setup.py": {"bytes": 1, "sha256": "1" * 64}}
    monkeypatch.setattr(
        helper,
        "_rules_source_provenance",
        lambda *_args: ("a" * 40, source_files),
    )

    def artifact(path: Path) -> dict[str, object]:
        return {
            "bytes": path.stat().st_size,
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    python = Path(sys.executable).resolve()
    manifest: dict[str, object] = {
        "binding": artifact(binding),
        "build": {
            "command": [str(python), "setup.py", "build_ext"],
            "environment": {},
            "returncode": 0,
            "stderr": artifact(stderr),
            "stdout": artifact(stdout),
        },
        "python": {
            **artifact(python),
            "cache_tag": sys.implementation.cache_tag,
            "compiler": sys.version,
            "platform": helper.sysconfig.get_platform(),
            "runtime_libraries": helper._python_runtime_artifacts(),
        },
        "python_build_dependencies": {
            "setuptools": helper._distribution_version("setuptools"),
            "wheel": helper._distribution_version("wheel"),
        },
        "schema": helper.BUILD_MANIFEST_SCHEMA,
        "source": {
            "commit": "a" * 40,
            "files": source_files,
            "root": str(tmp_path.resolve()),
        },
        "toolchain": {
            "cl.exe": artifact(cl),
            "link.exe": artifact(link),
        },
    }
    native = artifact(loaded)
    return manifest, native, binding, cl


def _write_synthetic_manifest(
    path: Path, manifest: dict[str, object]
) -> str:
    wire = helper._canonical_json_bytes(manifest) + b"\n"
    path.write_bytes(wire)
    return hashlib.sha256(wire).hexdigest()


def test_manifest_reauthenticates_complete_build_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, native, original_binding, _cl = _synthetic_build_manifest(
        tmp_path, monkeypatch
    )
    path = tmp_path / "manifest.json"
    digest = _write_synthetic_manifest(path, manifest)
    identity = helper._load_build_manifest(
        path,
        digest,
        native_binding=native,
        source_root=tmp_path,
        source_commit="a" * 40,
        manifest_binding_path=original_binding,
    )
    assert identity["sha256"] == digest


@pytest.mark.parametrize(
    "mutation",
    ("extra-top-level", "failed-build", "extra-tool"),
)
def test_manifest_rejects_malformed_build_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    manifest, native, original_binding, _cl = _synthetic_build_manifest(
        tmp_path, monkeypatch
    )
    if mutation == "extra-top-level":
        manifest["unexpected"] = True
    elif mutation == "failed-build":
        assert isinstance(manifest["build"], dict)
        manifest["build"]["returncode"] = 1
    else:
        assert isinstance(manifest["toolchain"], dict)
        manifest["toolchain"]["unexpected.exe"] = copy.deepcopy(
            manifest["toolchain"]["cl.exe"]
        )
    path = tmp_path / "manifest.json"
    digest = _write_synthetic_manifest(path, manifest)
    with pytest.raises(helper.AtomicOutcomeProvenanceError):
        helper._load_build_manifest(
            path,
            digest,
            native_binding=native,
            source_root=tmp_path,
            source_commit="a" * 40,
            manifest_binding_path=original_binding,
        )


def test_manifest_rejects_toolchain_artifact_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, native, original_binding, cl = _synthetic_build_manifest(
        tmp_path, monkeypatch
    )
    path = tmp_path / "manifest.json"
    digest = _write_synthetic_manifest(path, manifest)
    cl.write_bytes(b"changed")
    with pytest.raises(
        helper.AtomicOutcomeProvenanceError, match="toolchain cl.exe artifact"
    ):
        helper._load_build_manifest(
            path,
            digest,
            native_binding=native,
            source_root=tmp_path,
            source_commit="a" * 40,
            manifest_binding_path=original_binding,
        )


def test_builder_refuses_output_reuse(
    native_binding: binding_builder.NativeBindingBuild,
) -> None:
    with pytest.raises(FileExistsError, match="refusing to reuse"):
        binding_builder.build_native_rules_binding(
            REPO_ROOT,
            native_binding.manifest_path.parent,
        )
