from __future__ import annotations

import copy
from pathlib import Path
import sys
from types import MappingProxyType, ModuleType, SimpleNamespace

import pytest

from tools.atomic_mining import atomic_outcome_helper
from tools.atomic_mining import run_e00_source as runner
from tools.atomic_mining import uci_session


START = "8/8/8/8/8/8/4P3/4K2k w - - 0 1"
OPTIONS = (
    ("UCI_Variant", "atomic"),
    ("Threads", 1),
    ("Hash", 512),
    ("MultiPV", 1),
    ("Ponder", False),
    ("SyzygyPath", ""),
    ("SyzygyProbeLimit", 0),
    ("Use NNUE", "true"),
)
IMPORT_INVENTORY = (
    {
        "source": "pyffish",
        "path": "pyffish",
        "sha256": "b" * 64,
        "size_bytes": 1,
        "module_names": ["pyffish"],
    },
)


def _request(tmp_path: Path, *, base_ms: int = 2) -> runner.LegRequest:
    files: dict[str, Path] = {}
    for name in (
        "engine.exe",
        "current.nnue",
        "teacher.nnue",
        "book.epd",
        "variants.ini",
        "pyffish.pyd",
        "build.json",
        "helper.py",
        "builder.py",
        "uci.py",
        "owned.py",
    ):
        path = tmp_path / name
        path.write_bytes(name.encode("ascii"))
        files[name] = path
    pair = runner.SchedulePair(
        experiment_id="exp",
        battery_id="battery",
        seed="seed",
        pair_id="a" * 64,
        pair_ordinal=1,
        stratum="VSTC",
        time_control={
            "base_ms": base_ms,
            "increment_ms": 0,
            "name": "VSTC",
        },
        book_sha256="b" * 64,
        book_line=1,
        root_fen=START,
        root_fen_sha256=runner.common.derive_id(
            "atomic-root-fen-v1", START
        ),
    )
    roles = runner._leg_roles(0)
    digest = "b" * 64
    return runner.LegRequest(
        pair=pair,
        leg=0,
        white_network_role=roles[0],
        black_network_role=roles[1],
        engine=files["engine.exe"],
        engine_sha256=digest,
        current_net=files["current.nnue"],
        current_net_sha256=digest,
        teacher_net=files["teacher.nnue"],
        teacher_net_sha256=digest,
        book=files["book.epd"],
        book_sha256=digest,
        variant_config=files["variants.ini"],
        variant_config_sha256=digest,
        pyffish=files["pyffish.pyd"],
        pyffish_sha256=digest,
        pyffish_manifest_binding_path=files["pyffish.pyd"],
        pyffish_build_manifest=files["build.json"],
        pyffish_build_manifest_sha256=digest,
        source_root=tmp_path,
        source_commit="c" * 40,
        helper=files["helper.py"],
        helper_sha256=digest,
        builder=files["builder.py"],
        builder_sha256=digest,
        uci_module=files["uci.py"],
        uci_module_sha256=digest,
        owned_process_module=files["owned.py"],
        owned_process_module_sha256=digest,
        runtime_package_root=tmp_path,
        child_import_inventory=IMPORT_INVENTORY,
        uci_options=OPTIONS,
        maximum_plies=8,
        command_timeout_seconds=1.0,
        leg_deadline_seconds=5.0,
    )


def _spec(name: str, kind: str) -> uci_session.UciOptionSpec:
    minimum = 1 if kind == "spin" else None
    maximum = 4096 if kind == "spin" else None
    choices = ("atomic",) if kind == "combo" else ()
    return uci_session.UciOptionSpec(
        name=name,
        kind=kind,  # type: ignore[arg-type]
        default="1" if kind == "spin" else "",
        minimum=minimum,
        maximum=maximum,
        choices=choices,
        raw=f"option name {name} type {kind}",
    )


class _FakeEngine:
    created: list["_FakeEngine"] = []
    completion_ns = 2_000_000

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        kinds = {
            "UCI_Variant": "combo",
            "Threads": "spin",
            "Hash": "spin",
            "MultiPV": "spin",
            "Ponder": "check",
            "SyzygyPath": "string",
            "SyzygyProbeLimit": "spin",
            "Use NNUE": "combo",
            "EvalFile": "string",
            "VariantPath": "string",
        }
        self.option_specs = MappingProxyType(
            {name: _spec(name, kind) for name, kind in kinds.items()}
        )
        self.engine_ids = MappingProxyType(
            {
                "name": "Atomic-Stockfish 1.0.3",
                "author": (
                    "the Atomic-Stockfish developers (see AUTHORS file)"
                ),
            }
        )
        self.handshake_preamble = runner.ENGINE_HANDSHAKE_PREAMBLE
        self.handshake_identity_order = (
            runner.ENGINE_HANDSHAKE_IDENTITY_ORDER
        )
        self.handshake_blank_after_ids = True
        self.applied_options: tuple[uci_session.UciOptionSetting, ...] = ()
        self.stderr = ""
        self.new_games = 0
        self.plays: list[dict[str, object]] = []
        type(self).created.append(self)

    def __enter__(self) -> "_FakeEngine":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def set_options(
        self,
        settings: tuple[uci_session.UciOptionSetting, ...],
        *,
        timeout: float,
    ) -> None:
        assert timeout == 1.0
        self.applied_options = tuple(settings)

    def new_game(self, *, timeout: float) -> None:
        assert timeout == 1.0
        self.new_games += 1

    def search(
        self, root_fen: str, *, nodes: int, timeout: float
    ) -> SimpleNamespace:
        assert root_fen == START
        assert nodes == 1
        assert timeout == 1.0
        role = (
            "current-v3"
            if "current.nnue"
            in str(self.applied_options[-1].value)
            else "run3b"
        )
        backend = runner.NETWORK_BACKENDS[role]
        path = self.applied_options[-1].value
        line = (
            "info string NNUE evaluation using "
            f"{backend} {path} (1MiB, (1, 1, 1, 1, 1))"
        )
        return SimpleNamespace(
            bestmove="e2e4",
            raw_lines=(line, "bestmove e2e4"),
        )

    def play_clocked(self, root_fen: str, **kwargs: object) -> object:
        assert root_fen == START
        self.plays.append(dict(kwargs))
        role = (
            "current-v3"
            if "current.nnue"
            in str(self.applied_options[-1].value)
            else "run3b"
        )
        backend = runner.NETWORK_BACKENDS[role]
        path = self.applied_options[-1].value
        marker = (
            "info string NNUE evaluation using "
            f"{backend} {path} (1MiB, (1, 1, 1, 1, 1))"
        )
        return uci_session.ClockedPlayResult(
            bestmove="e2e4",
            ponder=None,
            infos=(),
            raw_lines=(marker, "bestmove e2e4"),
            go_started_ns=0,
            bestmove_completed_ns=type(self).completion_ns,
        )


def _install_native_stubs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner,
        "_internal_request_bindings",
        lambda _request: (),
    )
    monkeypatch.setattr(
        runner,
        "_native_runtime",
        lambda _request: (object(), {"sealed": True}),
    )
    monkeypatch.setattr(
        runner,
        "_assert_child_import_inventory",
        lambda _request: [dict(row) for row in IMPORT_INVENTORY],
    )

    outcomes = iter(
        (
            atomic_outcome_helper.AtomicOutcome(
                False, "ongoing", "*", 0, START
            ),
            atomic_outcome_helper.AtomicOutcome(
                True, "checkmate", "1-0", -1, START
            ),
        )
    )

    def outcome(
        _native: object,
        _root: str,
        moves: object,
        calls: list[dict[str, object]],
    ) -> atomic_outcome_helper.AtomicOutcome:
        calls.append(
            runner._rules_call(
                "outcome",
                {"moves": list(moves)},
                {"terminal": bool(list(moves))},
            )
        )
        return next(outcomes)

    def legal(
        _native: object,
        _root: str,
        moves: object,
        calls: list[dict[str, object]],
    ) -> tuple[str, ...]:
        calls.append(
            runner._rules_call(
                "legal-moves", {"moves": list(moves)}, ["e2e4"]
            )
        )
        return ("e2e4",)

    monkeypatch.setattr(runner, "_native_outcome", outcome)
    monkeypatch.setattr(runner, "_native_legal_moves", legal)
    monkeypatch.setattr(uci_session, "UciEngine", _FakeEngine)


def test_direct_native_loop_uses_persistent_engines_and_equality_is_on_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FakeEngine.created = []
    _FakeEngine.completion_ns = 2_000_000
    _install_native_stubs(monkeypatch)

    request = _request(tmp_path, base_ms=2)
    execution = runner._play_direct(request)

    assert execution.result_white == "1-0"
    assert execution.time_loss is False
    assert execution.moves == ("e2e4",)
    assert len(_FakeEngine.created) == 2
    assert [engine.new_games for engine in _FakeEngine.created] == [1, 1]
    assert len(_FakeEngine.created[0].plays) == 1
    assert len(_FakeEngine.created[1].plays) == 0
    event = execution.referee_evidence["timing_events"][0]
    assert event["elapsed_ns"] == event["remaining_before_ns"]
    assert event["on_time"] is True
    assert event["move_applied"] is True
    assert event["remaining_after_ns"] == 0
    assert [
        call["operation"]
        for call in execution.referee_evidence["rules_calls"]
    ] == ["outcome", "legal-moves", "outcome"]
    runner._validate_engine_evidence(execution.engine_evidence, request)

    int_flag = copy.deepcopy(execution.engine_evidence)
    int_flag["engines"][0]["handshake"][
        "observed_blank_after_ids"
    ] = 1
    with pytest.raises(runner.E00RunnerError, match="must be bool"):
        runner._validate_engine_evidence(int_flag, request)

    drift = copy.deepcopy(execution.engine_evidence)
    handshake = drift["engines"][0]["handshake"]
    handshake["observed_preamble"] = ["changed banner"]
    handshake["sha256"] = runner._digest_document(
        "atomic-e00-uci-handshake-v1",
        {
            key: handshake[key]
            for key in (
                "expected_preamble",
                "observed_preamble",
                "expected_identity_order",
                "observed_identity_order",
                "expect_single_blank_after_ids",
                "observed_blank_after_ids",
            )
        },
    )
    with pytest.raises(runner.E00RunnerError, match="handshake evidence differs"):
        runner._validate_engine_evidence(drift, request)


def test_direct_native_loop_flags_one_nanosecond_late_without_applying_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FakeEngine.created = []
    _FakeEngine.completion_ns = 2_000_001
    monkeypatch.setattr(
        runner,
        "_internal_request_bindings",
        lambda _request: (),
    )
    monkeypatch.setattr(
        runner,
        "_native_runtime",
        lambda _request: (object(), {"sealed": True}),
    )
    monkeypatch.setattr(
        runner,
        "_assert_child_import_inventory",
        lambda _request: [dict(row) for row in IMPORT_INVENTORY],
    )
    monkeypatch.setattr(
        runner,
        "_native_outcome",
        lambda _native, _root, moves, calls: (
            calls.append(
                runner._rules_call(
                    "outcome", {"moves": list(moves)}, {"terminal": False}
                )
            )
            or atomic_outcome_helper.AtomicOutcome(
                False, "ongoing", "*", 0, START
            )
        ),
    )
    monkeypatch.setattr(
        runner,
        "_native_legal_moves",
        lambda _native, _root, moves, calls: (
            calls.append(
                runner._rules_call(
                    "legal-moves", {"moves": list(moves)}, ["e2e4"]
                )
            )
            or ("e2e4",)
        ),
    )
    monkeypatch.setattr(
        runner,
        "_native_insufficient_material",
        lambda _native, _root, moves, calls: (
            calls.append(
                runner._rules_call(
                    "insufficient-material",
                    {"moves": list(moves)},
                    {"white": False, "black": False},
                )
            )
            or (False, False)
        ),
    )
    monkeypatch.setattr(uci_session, "UciEngine", _FakeEngine)

    execution = runner._play_direct(_request(tmp_path, base_ms=2))

    assert execution.result_white == "0-1"
    assert execution.time_loss is True
    assert execution.terminal_reason == "time-loss"
    assert execution.moves == ()
    event = execution.referee_evidence["timing_events"][0]
    assert event["elapsed_ns"] == event["remaining_before_ns"] + 1
    assert event["on_time"] is False
    assert event["move_applied"] is False
    assert event["remaining_after_ns"] is None


@pytest.mark.skipif(
    runner.os.name != "nt", reason="production immutability is Windows"
)
def test_artifact_lock_blocks_write_and_replace_restore(
    tmp_path: Path,
) -> None:
    path = tmp_path / "engine.exe"
    original = b"sealed-engine"
    path.write_bytes(original)
    digest = runner.common.sha256_bytes(original)
    binding = runner._artifact("engine", path, digest)
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(b"mutated-engine")
    try:
        with pytest.raises(PermissionError):
            path.write_bytes(b"transient")
        with pytest.raises(PermissionError):
            runner.os.replace(replacement, path)
        runner._verify_artifact_unchanged(binding)
        assert binding.identity["size_bytes"] == len(original)
    finally:
        binding.close()
    runner.os.replace(replacement, path)
    assert path.read_bytes() == b"mutated-engine"


@pytest.mark.skipif(
    runner.os.name != "nt", reason="production immutability is Windows"
)
def test_artifact_lock_rejects_reparse_path(tmp_path: Path) -> None:
    target = tmp_path / "target.bin"
    target.write_bytes(b"target")
    link = tmp_path / "link.bin"
    try:
        link.symlink_to(target)
    except OSError as error:
        pytest.skip(f"symlink privilege unavailable: {error}")
    with pytest.raises(runner.E00RunnerError, match="reparse"):
        runner._artifact(
            "linked artifact",
            link,
            runner.common.sha256_bytes(target.read_bytes()),
        )


@pytest.mark.skipif(
    runner.os.name != "nt", reason="production namespace guard is Windows"
)
def test_artifact_guard_blocks_parent_directory_rename(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "inputs"
    parent.mkdir()
    path = parent / "engine.exe"
    path.write_bytes(b"sealed")
    binding = runner._artifact(
        "engine", path, runner.common.sha256_bytes(b"sealed")
    )
    try:
        with pytest.raises(PermissionError):
            runner.os.replace(parent, tmp_path / "renamed-inputs")
        runner._verify_artifact_unchanged(binding)
    finally:
        binding.close()


@pytest.mark.skipif(
    runner.os.name != "nt", reason="production namespace guard is Windows"
)
def test_directory_guard_blocks_leaf_and_parent_rename_and_seals_inventory(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "output-parent"
    target = parent / "runtime-package"
    target.mkdir(parents=True)
    (target / "module.py").write_bytes(b"sealed")
    binding = runner._directory_binding("runtime", target)
    inventory = runner._verify_directory_binding(binding)
    try:
        leaf_renamed = False
        try:
            runner.os.replace(target, parent / "runtime-renamed")
        except PermissionError:
            pass
        else:
            leaf_renamed = True
            with pytest.raises(runner.E00RunnerError):
                binding._guard.verify()
            runner.os.replace(parent / "runtime-renamed", target)
        assert leaf_renamed or target.is_dir()
        parent_renamed = False
        try:
            runner.os.replace(parent, tmp_path / "output-renamed")
        except PermissionError:
            pass
        else:
            parent_renamed = True
            with pytest.raises(runner.E00RunnerError):
                binding._guard.verify()
            runner.os.replace(tmp_path / "output-renamed", parent)
        assert parent_renamed or parent.is_dir()
        runner._verify_directory_binding(
            binding, expected_inventory=inventory
        )
        (target / "late.py").write_bytes(b"late")
        with pytest.raises(
            runner.E00RunnerError, match="enumeration changed"
        ):
            runner._verify_directory_binding(
                binding, expected_inventory=inventory
            )
    finally:
        binding.close()


@pytest.mark.skipif(
    runner.os.name != "nt", reason="strict namespace seal is Windows"
)
def test_strict_static_namespace_detects_uncommitted_entry_creation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sealed-root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    module = nested / "module.py"
    module.write_bytes(b"sealed")
    artifact = runner._artifact(
        "sealed module",
        module,
        runner.common.sha256_bytes(b"sealed"),
    )
    seal: runner.DirectoryBinding | None = None
    try:
        runner._delegate_parent_locks((artifact,))
        seal = runner._directory_binding(
            "strict root", root, strict_static=True
        )
        inventory = runner._verify_directory_binding(seal)
        # A Windows directory sharing handle prevents rename/delete but not
        # FILE_ADD_FILE. Under the cooperative-host threat model, an
        # unreferenced addition cannot execute by itself; it is nevertheless
        # detected by the next exact checkpoint and retained for forensics.
        (nested / "late.py").write_bytes(b"late")
        with pytest.raises(
            runner.E00RunnerError, match="enumeration changed"
        ):
            runner._verify_directory_binding(
                seal, expected_inventory=inventory
            )
        runner._verify_artifact_unchanged(artifact)
    finally:
        if seal is not None:
            seal.close()
        artifact.close()


def test_child_import_inventory_rejects_late_file_backed_import(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    baseline = runner._file_backed_module_inventory(
        runtime_package_root=request.runtime_package_root,
        pyffish_path=request.pyffish,
    )
    request = runner.replace(
        request,
        child_import_inventory=tuple(dict(row) for row in baseline),
    )
    late_path = tmp_path / "late_module.py"
    late_path.write_bytes(b"x = 1\n")
    late = ModuleType("atomic_e00_late_test_module")
    late.__file__ = str(late_path)
    sys.modules[late.__name__] = late
    try:
        with pytest.raises(
            runner.E00RunnerError, match="extras, omissions"
        ):
            runner._assert_child_import_inventory(request)
    finally:
        sys.modules.pop(late.__name__, None)


@pytest.mark.skipif(
    runner.os.name != "nt", reason="production cleanup contract is Windows"
)
def test_scoped_failure_releases_file_and_directory_guards(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "controlled"
    parent.mkdir()
    artifact = parent / "input.bin"
    artifact.write_bytes(b"sealed")

    @runner._artifact_guard_scoped
    def fail_after_binding() -> None:
        runner._artifact(
            "input",
            artifact,
            runner.common.sha256_bytes(b"sealed"),
        )
        runner._directory_binding("controlled", parent)
        with pytest.raises(PermissionError):
            artifact.write_bytes(b"blocked")
        with pytest.raises(PermissionError):
            runner.os.replace(parent, tmp_path / "blocked-rename")
        raise RuntimeError("synthetic primary failure")

    with pytest.raises(RuntimeError, match="synthetic primary"):
        fail_after_binding()
    artifact.write_bytes(b"released")
    renamed = tmp_path / "released-rename"
    runner.os.replace(parent, renamed)
    assert (renamed / "input.bin").read_bytes() == b"released"


def test_dual_scope_cleanup_attempts_directory_after_file_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fail_files(_guards: object) -> None:
        calls.append("files")
        raise RuntimeError("synthetic file cleanup failure")

    def close_directories(_guards: object) -> None:
        calls.append("directories")

    monkeypatch.setattr(runner, "_close_guard_scope", fail_files)
    monkeypatch.setattr(
        runner, "_close_directory_guard_scope", close_directories
    )
    with pytest.raises(
        runner.E00RunnerError, match="file and namespace"
    ):
        runner._close_all_guard_scopes([], [])
    assert calls == ["files", "directories"]
