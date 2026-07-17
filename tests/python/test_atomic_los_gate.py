from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest


TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import atomic_compiler_preflight as compiler_preflight
import atomic_los_gate as los_gate


class FakeRunner:
    @staticmethod
    def elo_stats(scores):
        marker = {98: 0.9994, 99: 0.9996, 100: 1.0}.get(scores[0], 0.5)
        return "ELO: 0.00 +-0.0 (95%%) LOS: %.1f%%\n" % (100.0 * marker)


CONFIG = los_gate.GateConfig(
    min_total_exclusive=100, target_los_display="100.0"
)


def valid_match(**overrides):
    values = {
        "max_games": 64000,
        "sprt": False,
        "variants": ["atomic"],
        "variant": "atomic",
        "time": 2000,
        "inc": 20,
        "threads": 4,
        "verbosity": 2,
        "book": True,
        "config": "variants.ini",
        "engine_options": [
            {
                "Use NNUE": "true",
                "Threads": "1",
                "Hash": "512",
                "EvalFile": "net.nnue",
            },
            {
                "Use NNUE": "true",
                "Threads": "1",
                "Hash": "512",
                "EvalFile": "net.nnue",
            },
        ],
        "engine_paths": ["candidate.exe", "baseline.exe"],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_gate_requires_more_than_threshold_and_a_complete_pair():
    assert not los_gate.evaluate_gate(FakeRunner, [100, -1, 3], CONFIG).passed

    at_threshold = los_gate.evaluate_gate(FakeRunner, [100, 0, 0], CONFIG)
    assert at_threshold.total == 100
    assert not at_threshold.passed

    incomplete_pair = los_gate.evaluate_gate(FakeRunner, [100, 0, 1], CONFIG)
    assert incomplete_pair.total == 101
    assert not incomplete_pair.complete_pairs
    assert not incomplete_pair.passed

    passed = los_gate.evaluate_gate(FakeRunner, [99, 0, 3], CONFIG)
    assert passed.total == 102
    assert passed.complete_pairs
    assert passed.displayed_los == "100.0"
    assert passed.passed


@pytest.mark.parametrize(
    ("wins", "displayed", "passed"),
    [(98, "99.9", False), (99, "100.0", True)],
)
def test_gate_uses_runner_one_decimal_rounding(wins, displayed, passed):
    decision = los_gate.evaluate_gate(FakeRunner, [wins, 0, 102 - wins], CONFIG)
    assert decision.displayed_los == displayed
    assert decision.passed is passed


@pytest.mark.parametrize(
    "elo_stats",
    [
        lambda scores: "\n",
        lambda scores: (_ for _ in ()).throw(ValueError("all draws")),
        lambda scores: (_ for _ in ()).throw(ZeroDivisionError()),
    ],
)
def test_degenerate_statistics_never_pass(elo_stats):
    runner = SimpleNamespace(elo_stats=elo_stats)
    decision = los_gate.evaluate_gate(runner, [0, 0, 102], CONFIG)
    assert decision.displayed_los is None
    assert not decision.passed


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"max_games": 63999}, "exactly 64000"),
        ({"sprt": True}, "SPRT"),
        ({"variants": ["atomic", "atomic960"]}, "exactly"),
        ({"time": 5000, "inc": 50}, "one of"),
        ({"threads": 3}, "exactly 4"),
        ({"verbosity": 1}, "exactly 2"),
        ({"book": False}, "opening book"),
        ({"config": None}, "variants.ini"),
    ],
)
def test_normative_configuration_rejects_any_runner_drift(overrides, message):
    with pytest.raises(los_gate.GateConfigurationError, match=message):
        los_gate.validate_match_configuration(valid_match(**overrides), CONFIG)


def test_normative_configuration_accepts_exact_three_time_controls():
    for base, increment in ((2000, 20), (10000, 100), (30000, 300)):
        los_gate.validate_match_configuration(
            valid_match(time=base, inc=increment), CONFIG
        )


@pytest.mark.parametrize(
    ("option_name", "value", "message"),
    [
        ("Use NNUE", "pure", "data generation"),
        ("Use NNUE", "false", "Use NNUE=true"),
        ("Threads", "2", "Threads"),
        ("Hash", "64", "Hash"),
    ],
)
def test_normative_configuration_rejects_engine_option_drift(
    option_name, value, message
):
    options = [dict(item) for item in valid_match().engine_options]
    options[0][option_name] = value
    with pytest.raises(los_gate.GateConfigurationError, match=message):
        los_gate.validate_match_configuration(
            valid_match(engine_options=options), CONFIG
        )


def test_normative_configuration_requires_evalfile_for_both_engines():
    options = [dict(item) for item in valid_match().engine_options]
    options[1].pop("EvalFile")
    with pytest.raises(los_gate.GateConfigurationError, match="evalfile"):
        los_gate.validate_match_configuration(
            valid_match(engine_options=options), CONFIG
        )


def test_normative_configuration_rejects_extra_engine_options():
    options = [dict(item) for item in valid_match().engine_options]
    options[0]["Skill Level"] = "0"
    with pytest.raises(los_gate.GateConfigurationError, match="non-normative"):
        los_gate.validate_match_configuration(
            valid_match(engine_options=options), CONFIG
        )


def test_normative_configuration_rejects_non_normative_gate_contract():
    with pytest.raises(los_gate.GateConfigurationError, match="Total > 100"):
        los_gate.validate_match_configuration(
            valid_match(), los_gate.GateConfig(99, "100.0")
        )


def test_cooperative_stop_delegates_until_final_gate_passes():
    class BaseMatch:
        def __init__(self):
            self.scores = [98, 0, 4]
            self.base_stop_calls = 0

        def stop(self):
            self.base_stop_calls += 1
            return False

    runner = SimpleNamespace(EngineMatch=BaseMatch, elo_stats=FakeRunner.elo_stats)
    match = los_gate.make_gated_match_class(runner, CONFIG)()

    assert not match.stop()
    assert match.base_stop_calls == 1
    assert not match.gate_observed

    match.scores = [99, 0, 3]
    assert match.stop()
    assert match.base_stop_calls == 1
    assert match.gate_observed


def test_imports_runner_only_when_exact_sha_is_supplied(tmp_path):
    runner_path = tmp_path / "variantfishtest_new1.py"
    runner_path.write_text(
        "class EngineMatch:\n    pass\n"
        "def elo_stats(scores):\n    return 'LOS: 100.0%'\n",
        encoding="utf-8",
    )
    runner_sha = compiler_preflight.fingerprint_file(
        runner_path, label="runner"
    ).sha256
    loaded = los_gate.load_runner_module(
        runner_path,
        expected_sha256=runner_sha,
        expected_stat_util_sha256=None,
        expected_chess_init_sha256=None,
        expected_chess_uci_sha256=None,
        expected_python_version=None,
        expected_python_executable_sha256=None,
        expected_python_runtime_sha256=None,
        expected_psutil_version=None,
        expected_psutil_module_sha256=None,
    )
    assert loaded.__file__ == str(runner_path)
    assert loaded.elo_stats([1, 0, 0]) == "LOS: 100.0%"
    assert loaded.__atomic_source_fingerprints__[0].sha256 == runner_sha


def test_runner_import_rejects_wrong_psutil_version(tmp_path):
    runner_path = tmp_path / "variantfishtest_new1.py"
    runner_path.write_text(
        "class EngineMatch:\n    pass\n"
        "def elo_stats(scores):\n    return 'LOS: 100.0%'\n",
        encoding="utf-8",
    )
    runner_sha = compiler_preflight.fingerprint_file(
        runner_path, label="runner"
    ).sha256
    with pytest.raises(los_gate.GateConfigurationError, match="psutil version"):
        los_gate.load_runner_module(
            runner_path,
            expected_sha256=runner_sha,
            expected_stat_util_sha256=None,
            expected_chess_init_sha256=None,
            expected_chess_uci_sha256=None,
            expected_python_version=None,
            expected_python_executable_sha256=None,
            expected_python_runtime_sha256=None,
            expected_psutil_version="0.0-test",
            expected_psutil_module_sha256=None,
        )


def test_psutil_provenance_covers_exact_loaded_module_set(
    tmp_path, monkeypatch
):
    package = tmp_path / "psutil"
    package.mkdir()
    expected_hashes = {}
    for module_name, label in los_gate.PSUTIL_MODULE_LABELS.items():
        suffix = ".pyd" if module_name == "psutil._psutil_windows" else ".py"
        name = "__init__" if module_name == "psutil" else module_name.rsplit(".", 1)[1]
        path = package / f"{name}{suffix}"
        path.write_bytes(module_name.encode("ascii"))
        monkeypatch.setitem(sys.modules, module_name, SimpleNamespace(__file__=path))
        expected_hashes[module_name] = compiler_preflight.fingerprint_file(
            path, label=label
        ).sha256

    monkeypatch.setattr(los_gate.psutil, "__version__", "test-version")
    fingerprints = los_gate.normative_psutil_fingerprints(
        expected_version="test-version", expected_hashes=expected_hashes
    )
    assert tuple(item.label for item in fingerprints) == tuple(
        los_gate.PSUTIL_MODULE_LABELS.values()
    )
    compiler_preflight.verify_file_fingerprints(fingerprints)


def test_default_runner_hash_rejects_modified_external_runner(tmp_path):
    runner_path = tmp_path / "variantfishtest_new1.py"
    runner_path.write_text("modified = True\n", encoding="utf-8")
    with pytest.raises(los_gate.GateConfigurationError, match="external.*runner"):
        los_gate.load_runner_module(runner_path)


def test_normative_asset_preflight_hashes_every_input_and_emits_clear_pass(
    tmp_path, monkeypatch, capsys
):
    runner_path = tmp_path / "variantfishtest_new1.py"
    stat_util_path = tmp_path / "stat_util.py"
    python_executable = tmp_path / "python.exe"
    python_runtime = tmp_path / "python312.dll"
    chess_init = tmp_path / "chess-init.py"
    chess_uci = tmp_path / "chess-uci.py"
    psutil_files = {
        "psutil_init": tmp_path / "psutil-init.py",
        "psutil_common": tmp_path / "psutil-common.py",
        "psutil_ntuples": tmp_path / "psutil-ntuples.py",
        "psutil_native": tmp_path / "psutil-native.pyd",
        "psutil_windows": tmp_path / "psutil-windows.py",
    }
    candidate = tmp_path / "candidate.exe"
    baseline = tmp_path / "baseline.exe"
    config = tmp_path / "variants.ini"
    book_dir = tmp_path / "books"
    book_dir.mkdir()
    alias_dir = tmp_path / "alias"
    alias_dir.mkdir()
    book = book_dir / "atomic.epd"
    net = tmp_path / "network.nnue"
    for path, content in (
        (runner_path, b"runner"),
        (stat_util_path, b"stat-util"),
        (python_executable, b"python-executable"),
        (python_runtime, b"python-runtime"),
        (chess_init, b"chess-init"),
        (chess_uci, b"chess-uci"),
        *((path, label.encode("ascii")) for label, path in psutil_files.items()),
        (candidate, b"candidate"),
        (baseline, b"baseline"),
        (config, b"config"),
        (book, b"book"),
        (net, b"net"),
    ):
        path.write_bytes(content)

    fingerprints = {
        label: compiler_preflight.fingerprint_file(path, label=label)
        for label, path in (
            ("runner", runner_path),
            ("stat_util", stat_util_path),
            ("python_executable", python_executable),
            ("python_runtime", python_runtime),
            ("chess_init", chess_init),
            ("chess_uci", chess_uci),
            *psutil_files.items(),
            ("baseline", baseline),
            ("config", config),
            ("book", book),
            ("net", net),
        )
    }
    monkeypatch.setattr(
        los_gate, "EXPECTED_RUNNER_SHA256", fingerprints["runner"].sha256
    )
    monkeypatch.setattr(
        los_gate,
        "EXPECTED_STAT_UTIL_SHA256",
        fingerprints["stat_util"].sha256,
    )
    monkeypatch.setattr(
        los_gate,
        "EXPECTED_PYTHON_EXECUTABLE_SHA256",
        fingerprints["python_executable"].sha256,
    )
    monkeypatch.setattr(
        los_gate,
        "EXPECTED_PYTHON_RUNTIME_SHA256",
        fingerprints["python_runtime"].sha256,
    )
    monkeypatch.setattr(
        los_gate,
        "EXPECTED_CHESS_INIT_SHA256",
        fingerprints["chess_init"].sha256,
    )
    monkeypatch.setattr(
        los_gate,
        "EXPECTED_CHESS_UCI_SHA256",
        fingerprints["chess_uci"].sha256,
    )
    monkeypatch.setattr(
        los_gate,
        "EXPECTED_PSUTIL_MODULE_SHA256",
        {
            module_name: fingerprints[label].sha256
            for module_name, label in los_gate.PSUTIL_MODULE_LABELS.items()
        },
    )
    monkeypatch.setattr(
        los_gate, "NORMATIVE_BASELINE_SHA256", fingerprints["baseline"].sha256
    )
    monkeypatch.setattr(
        los_gate, "EXPECTED_CONFIG_SHA256", fingerprints["config"].sha256
    )
    monkeypatch.setattr(
        los_gate, "EXPECTED_BOOK_SHA256", fingerprints["book"].sha256
    )
    monkeypatch.setattr(los_gate, "EXPECTED_NET_SHA256", fingerprints["net"].sha256)

    runner = SimpleNamespace(
        __file__=str(runner_path),
        __atomic_source_fingerprints__=(
            fingerprints["runner"],
            fingerprints["stat_util"],
            fingerprints["python_executable"],
            fingerprints["python_runtime"],
            fingerprints["chess_init"],
            fingerprints["chess_uci"],
            *(fingerprints[label] for label in psutil_files),
        ),
    )
    options = [
        {
            "Use NNUE": "true",
            "Threads": "1",
            "Hash": "512",
            "EvalFile": str(alias_dir / ".." / net.name),
        }
        for _ in range(2)
    ]
    match = valid_match(
        engine_paths=[
            str(alias_dir / ".." / candidate.name),
            str(alias_dir / ".." / baseline.name),
        ],
        engine_options=options,
        config=str(alias_dir / ".." / config.name),
        book=True,
    )
    snapshot = los_gate.validate_normative_assets(runner, match)

    assert len(snapshot.fingerprints) == 17
    assert match.engine_paths == [str(candidate.resolve()), str(baseline.resolve())]
    assert match.config == str(config.resolve())
    assert match.book == str(book.resolve())
    assert all(option["EvalFile"] == str(net.resolve()) for option in options)
    output = capsys.readouterr().out
    assert "Normative LOS assets: PASS" in output
    assert "python=CPython-3.12.0 chess=0.8.0 psutil=7.2.2" in output
    assert "tc=2000+20" in output
    assert "variants=['atomic']" in output
    assert "max_games=64000 workers=4 verbosity=2" in output


def _run_gate_fixture(
    tmp_path,
    *,
    mutate=False,
    close_error=False,
    run_exit=False,
    match_error=False,
    worker_error=False,
    stall=False,
    scores=None,
):
    events = []
    stable = tmp_path / "stable.bin"
    stable.write_bytes(b"stable")
    fingerprint = compiler_preflight.fingerprint_file(stable, label="stable")

    class Match:
        def __init__(self):
            self.scores = list([99, 0, 3] if scores is None else scores)
            self.__dict__.update(valid_match().__dict__)
            self.fens = ["startpos"]
            self.lock = threading.Lock()

        def stop(self):
            return False

        def print_settings(self):
            events.append("run")
            if run_exit:
                raise SystemExit(1)
            if mutate:
                stable.write_bytes(b"changed")

        def validate_engine_variants(self):
            pass

        def worker(self):
            if worker_error:
                raise RuntimeError("worker failed outside play_match_instance")
            if match_error:
                while True:
                    with self.lock:
                        if self.stop():
                            return
                    try:
                        self.play_match_instance()
                    except Exception:
                        continue
            if stall:
                while True:
                    with self.lock:
                        if self.stop():
                            return
                    threading.Event().wait(0.001)

        def play_match_instance(self):
            if match_error:
                raise RuntimeError("match instance failed")
            return (0, 0, 0, 0)

        def print_results(self):
            pass

        def close(self):
            events.append("close")
            if close_error:
                raise RuntimeError("close failed")

    runner = SimpleNamespace(
        EngineMatch=Match,
        elo_stats=FakeRunner.elo_stats,
        __file__="variantfishtest_new1.py",
    )

    def asset_preflight(runner_arg, match):
        events.append("asset_preflight")
        return los_gate.NormativeAssetSnapshot((fingerprint,))

    def preflight(match, assets):
        assert assets.fingerprints == (fingerprint,)
        events.append("compiler_preflight")
        return None

    return runner, events, asset_preflight, preflight


def test_los_wrapper_orders_asset_and_compiler_preflight_before_match(
    tmp_path, capsys
):
    runner, events, asset_preflight, preflight = _run_gate_fixture(tmp_path)
    code, decision = los_gate.run_gate(
        runner,
        ["candidate.exe", "baseline.exe"],
        CONFIG,
        preflight=preflight,
        asset_preflight=asset_preflight,
    )

    assert code == 0
    assert decision.passed
    assert events == ["asset_preflight", "compiler_preflight", "run", "close"]
    assert "LOS artifact postflight: PASS files=1" in capsys.readouterr().out


def test_los_postflight_turns_asset_mutation_into_hard_error(tmp_path):
    runner, events, asset_preflight, preflight = _run_gate_fixture(
        tmp_path, mutate=True
    )
    with pytest.raises(los_gate.GateConfigurationError, match="changed after preflight"):
        los_gate.run_gate(
            runner,
            ["candidate.exe", "baseline.exe"],
            CONFIG,
            preflight=preflight,
            asset_preflight=asset_preflight,
        )
    assert events[-2:] == ["run", "close"]


def test_los_postflight_still_runs_when_runner_close_fails(tmp_path, capsys):
    runner, events, asset_preflight, preflight = _run_gate_fixture(
        tmp_path, close_error=True
    )
    with pytest.raises(RuntimeError, match="close failed"):
        los_gate.run_gate(
            runner,
            ["candidate.exe", "baseline.exe"],
            CONFIG,
            preflight=preflight,
            asset_preflight=asset_preflight,
        )
    assert events[-2:] == ["run", "close"]
    assert "LOS artifact postflight: PASS files=1" in capsys.readouterr().out


def test_runner_system_exit_during_configuration_is_a_gate_error():
    class Match:
        def __init__(self):
            raise SystemExit(1)

    runner = SimpleNamespace(
        EngineMatch=Match,
        elo_stats=FakeRunner.elo_stats,
        __file__="variantfishtest_new1.py",
    )
    with pytest.raises(
        los_gate.GateConfigurationError, match="configuration with code 1"
    ):
        los_gate.run_gate(runner, ["candidate.exe", "baseline.exe"], CONFIG)


def test_runner_system_exit_during_execution_is_a_gate_error_and_closes(
    tmp_path, capsys
):
    runner, events, asset_preflight, preflight = _run_gate_fixture(
        tmp_path, run_exit=True
    )
    with pytest.raises(
        los_gate.GateConfigurationError, match="execution with code 1"
    ):
        los_gate.run_gate(
            runner,
            ["candidate.exe", "baseline.exe"],
            CONFIG,
            preflight=preflight,
            asset_preflight=asset_preflight,
        )
    assert events[-2:] == ["run", "close"]
    assert "LOS artifact postflight: PASS files=1" in capsys.readouterr().out


def test_first_match_instance_exception_invalidates_complete_gate(tmp_path):
    runner, events, asset_preflight, preflight = _run_gate_fixture(
        tmp_path, match_error=True, scores=[0, 0, 0]
    )
    with pytest.raises(
        los_gate.GateConfigurationError, match=r"failure budget \(1/1\)"
    ):
        los_gate.run_gate(
            runner,
            ["candidate.exe", "baseline.exe"],
            CONFIG,
            preflight=preflight,
            asset_preflight=asset_preflight,
        )
    assert events[-1] == "close"


def test_exception_outside_match_instance_is_not_lost_in_worker(tmp_path):
    runner, events, asset_preflight, preflight = _run_gate_fixture(
        tmp_path, worker_error=True
    )
    with pytest.raises(
        los_gate.GateConfigurationError,
        match="worker failed.*worker failed outside",
    ):
        los_gate.run_gate(
            runner,
            ["candidate.exe", "baseline.exe"],
            CONFIG,
            preflight=preflight,
            asset_preflight=asset_preflight,
        )
    assert events[-1] == "close"


def test_no_progress_watchdog_aborts_and_joins_workers(
    tmp_path, monkeypatch
):
    runner, events, asset_preflight, preflight = _run_gate_fixture(
        tmp_path, stall=True, scores=[0, 0, 0]
    )
    monkeypatch.setattr(los_gate, "MATCH_NO_PROGRESS_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(los_gate, "WORKER_JOIN_POLL_SECONDS", 0.001)
    with pytest.raises(
        los_gate.GateConfigurationError, match="no score progress"
    ):
        los_gate.run_gate(
            runner,
            ["candidate.exe", "baseline.exe"],
            CONFIG,
            preflight=preflight,
            asset_preflight=asset_preflight,
        )
    assert events[-1] == "close"


def test_keyboard_interrupt_forces_cleanup_and_joins_workers_before_close(
    tmp_path, monkeypatch
):
    runner, events, asset_preflight, preflight = _run_gate_fixture(tmp_path)
    instances = []
    interrupt_once = [True]

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon
            self.alive = False
            instances.append(self)

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            if interrupt_once[0]:
                interrupt_once[0] = False
                raise KeyboardInterrupt
            if self.alive:
                self.target()
                self.alive = False

    cleanup_calls = []
    monkeypatch.setattr(los_gate.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        los_gate,
        "cleanup_new_engine_processes",
        lambda match, pids, **kwargs: cleanup_calls.append(set(pids)) or (),
    )
    with pytest.raises(KeyboardInterrupt):
        los_gate.run_gate(
            runner,
            ["candidate.exe", "baseline.exe"],
            CONFIG,
            preflight=preflight,
            asset_preflight=asset_preflight,
        )
    assert len(instances) == 4
    assert all(not thread.is_alive() for thread in instances)
    assert cleanup_calls
    assert events[-1] == "close"


def test_main_reports_keyboard_interrupt_as_130(monkeypatch, capsys):
    monkeypatch.setattr(
        los_gate,
        "load_runner_module",
        lambda path: SimpleNamespace(__file__=str(path)),
    )

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(los_gate, "run_gate", interrupted)
    code = los_gate.main(
        [
            "--runner",
            "runner.py",
            "--min-total-exclusive",
            "100",
            "--target-los",
            "100.0",
            "--",
            "candidate.exe",
            "baseline.exe",
        ]
    )
    assert code == 130
    assert "interrupted" in capsys.readouterr().err


def test_cleanup_terminates_only_new_matching_engine_descendants(
    tmp_path, monkeypatch
):
    engine = (tmp_path / "candidate.exe").resolve()
    other = (tmp_path / "other.exe").resolve()

    class Child:
        def __init__(self, pid, executable):
            self.pid = pid
            self.executable = executable
            self.terminated = False
            self.killed = False

        def exe(self):
            return str(self.executable)

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    old_match = Child(10, engine)
    leaked_match = Child(11, engine)
    unrelated = Child(12, other)

    class Parent:
        def children(self, recursive=True):
            return [old_match, leaked_match, unrelated]

    def wait_procs(processes, timeout):
        processes = list(processes)
        if all(process.terminated or process.killed for process in processes):
            return processes, []
        return [], processes

    monkeypatch.setattr(los_gate.psutil, "Process", lambda: Parent())
    monkeypatch.setattr(los_gate.psutil, "wait_procs", wait_procs)
    match = SimpleNamespace(engine_paths=[str(engine)])
    cleaned = los_gate.cleanup_new_engine_processes(match, {10})

    assert cleaned == (11,)
    assert leaked_match.terminated
    assert not old_match.terminated
    assert not unrelated.terminated


def test_playing_smoke_requires_two_real_nnue_atomic_bestmoves(
    tmp_path, monkeypatch, capsys
):
    engines = [tmp_path / "candidate.exe", tmp_path / "baseline.exe"]
    nets = [tmp_path / "atomic-a.nnue", tmp_path / "atomic-b.nnue"]
    calls = []

    def completed(command, **kwargs):
        index = len(calls)
        calls.append((command, kwargs["input"]))
        marker = (
            los_gate.CANDIDATE_NNUE_ARCHITECTURE_MARKER
            if index == 0
            else "enabled"
        )
        prefix = (
            "info string NNUE evaluation using Legacy Atomic V1 "
            if index == 0
            else "info string NNUE evaluation using "
        )
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "uciok\n"
                f"{prefix}{nets[index]} {marker}\n"
                "readyok\ninfo depth 1 nodes 1\nbestmove a2a3\n"
            ),
        )

    monkeypatch.setattr(los_gate.subprocess, "run", completed)
    match = SimpleNamespace(
        engine_paths=[str(path) for path in engines],
        engine_options=[
            {
                "Use NNUE": "true",
                "Threads": "1",
                "Hash": "512",
                "EvalFile": str(net),
            }
            for net in nets
        ],
        config=str(tmp_path / "variants.ini"),
    )
    los_gate.playing_engine_smoke(match)

    assert len(calls) == 2
    assert all("go nodes 1" in commands for _, commands in calls)
    assert "LOS playing preflight: PASS" in capsys.readouterr().out


@pytest.mark.parametrize(
    "stdout, expected",
    (
        ("uciok\nreadyok\n", "no bestmove"),
        ("uciok\nreadyok\ninfo string ERROR: net\nbestmove a2a3\n", "error"),
        (
            "uciok\nreadyok\nbestmove a2a3\n",
            "did not confirm its expected",
        ),
    ),
)
def test_playing_smoke_rejects_incomplete_or_error_output(
    tmp_path, monkeypatch, stdout, expected
):
    net = tmp_path / "atomic.nnue"
    monkeypatch.setattr(
        los_gate.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=stdout),
    )
    match = SimpleNamespace(
        engine_paths=[str(tmp_path / "candidate.exe")],
        engine_options=[
            {
                "Use NNUE": "true",
                "Threads": "1",
                "Hash": "512",
                "EvalFile": str(net),
            }
        ],
        config=str(tmp_path / "variants.ini"),
    )
    with pytest.raises(los_gate.GateConfigurationError, match=expected):
        los_gate.playing_engine_smoke(match)


def test_playing_smoke_timeout_is_configuration_error(tmp_path, monkeypatch):
    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(los_gate.subprocess, "run", timed_out)
    match = SimpleNamespace(
        engine_paths=[str(tmp_path / "candidate.exe")],
        engine_options=[
            {
                "Use NNUE": "true",
                "Threads": "1",
                "Hash": "512",
                "EvalFile": str(tmp_path / "atomic.nnue"),
            }
        ],
        config=str(tmp_path / "variants.ini"),
    )
    with pytest.raises(los_gate.GateConfigurationError, match="timed out"):
        los_gate.playing_engine_smoke(match, timeout=0.1)


@pytest.mark.parametrize("scores", [[99, 0, 3], [98, 0, 4]])
def test_exit_zero_only_for_the_final_joined_gate(tmp_path, scores):
    runner, _, asset_preflight, preflight = _run_gate_fixture(tmp_path)
    original = runner.EngineMatch.__init__

    def init_with_scores(self):
        original(self)
        self.scores = list(scores)

    runner.EngineMatch.__init__ = init_with_scores
    code, decision = los_gate.run_gate(
        runner,
        ["candidate", "baseline"],
        CONFIG,
        preflight=preflight,
        asset_preflight=asset_preflight,
    )
    expected_code = 0 if scores[0] == 99 else 1
    assert code == expected_code
    assert decision.passed is (expected_code == 0)


def test_los_compiler_preflight_converts_compiler_error(monkeypatch, tmp_path):
    candidate = tmp_path / "candidate.exe"
    baseline = tmp_path / "baseline.exe"
    candidate.touch()
    baseline.touch()
    fingerprints = compiler_preflight.fingerprint_files(
        (("candidate", candidate), ("baseline", baseline))
    )
    match = SimpleNamespace(engine_paths=[str(candidate), str(baseline)])
    assets = los_gate.NormativeAssetSnapshot(fingerprints)

    def mismatch(*args, **kwargs):
        raise compiler_preflight.CompilerPreflightError("compiler version mismatch")

    monkeypatch.setattr(los_gate, "require_matching_compilation_settings", mismatch)
    with pytest.raises(los_gate.GateConfigurationError, match="compiler version"):
        los_gate.compiler_preflight(match, assets)


@pytest.mark.parametrize("value", ["nan", "inf", "100.01", "-0.1", "100.1"])
def test_target_los_rejects_degenerate_values(value):
    with pytest.raises(Exception):
        los_gate.parse_target_los(value)
