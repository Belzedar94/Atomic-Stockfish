from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER_PATH = REPO_ROOT / "tests" / "atomic_los_gate.py"
SPEC = importlib.util.spec_from_file_location("atomic_los_gate", WRAPPER_PATH)
assert SPEC is not None and SPEC.loader is not None
los_gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = los_gate
SPEC.loader.exec_module(los_gate)


class FakeRunner:
    @staticmethod
    def elo_stats(scores):
        marker = {98: 0.9994, 99: 0.9996, 100: 1.0}.get(scores[0], 0.5)
        return "ELO: 0.00 +-0.0 (95%%) LOS: %.1f%%\n" % (100.0 * marker)


CONFIG = los_gate.GateConfig(
    min_total_exclusive=100, target_los_display="100.0"
)


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
    [
        (98, "99.9", False),
        (99, "100.0", True),
    ],
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


def test_rejects_odd_limit_sprt_and_pure():
    valid = SimpleNamespace(
        max_games=64000,
        sprt=False,
        engine_options=[{"Use NNUE": "true"}, {"Use NNUE": "true"}],
    )
    los_gate.validate_match_configuration(valid, CONFIG)

    with pytest.raises(los_gate.GateConfigurationError, match="even"):
        los_gate.validate_match_configuration(
            SimpleNamespace(**{**valid.__dict__, "max_games": 63999}), CONFIG
        )
    with pytest.raises(los_gate.GateConfigurationError, match="SPRT"):
        los_gate.validate_match_configuration(
            SimpleNamespace(**{**valid.__dict__, "sprt": True}), CONFIG
        )
    with pytest.raises(los_gate.GateConfigurationError, match="data generation"):
        los_gate.validate_match_configuration(
            SimpleNamespace(
                **{
                    **valid.__dict__,
                    "engine_options": [{"use nnue": " PURE "}, {}],
                }
            ),
            CONFIG,
        )
    with pytest.raises(los_gate.GateConfigurationError, match="explicitly"):
        los_gate.validate_match_configuration(
            SimpleNamespace(
                **{
                    **valid.__dict__,
                    "engine_options": [{"Use NNUE": "false"}, {"Use NNUE": "true"}],
                }
            ),
            CONFIG,
        )
    with pytest.raises(los_gate.GateConfigurationError, match="explicitly"):
        los_gate.validate_match_configuration(
            SimpleNamespace(
                **{
                    **valid.__dict__,
                    "engine_options": [{"Use NNUE": "true"}, {}],
                }
            ),
            CONFIG,
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


def test_imports_runner_by_path(tmp_path):
    runner_path = tmp_path / "variantfishtest_new1.py"
    runner_path.write_text(
        "class EngineMatch:\n    pass\n"
        "def elo_stats(scores):\n    return 'LOS: 100.0%'\n",
        encoding="utf-8",
    )
    loaded = los_gate.load_runner_module(runner_path)
    assert loaded.__file__ == str(runner_path)
    assert loaded.elo_stats([1, 0, 0]) == "LOS: 100.0%"


@pytest.mark.parametrize(
    ("scores", "expected_code"),
    [([99, 0, 3], 0), ([98, 0, 4], 1)],
)
def test_exit_zero_only_for_the_final_joined_gate(scores, expected_code):
    class BaseMatch:
        def __init__(self):
            self.scores = list(scores)
            self.max_games = 64000
            self.sprt = False
            self.engine_options = [{"Use NNUE": "true"}, {"Use NNUE": "true"}]

        def stop(self):
            return False

        def run(self):
            pass

        def close(self):
            pass

    runner = SimpleNamespace(
        EngineMatch=BaseMatch,
        elo_stats=FakeRunner.elo_stats,
        __file__="variantfishtest_new1.py",
    )
    code, decision = los_gate.run_gate(runner, ["candidate", "baseline"], CONFIG)
    assert code == expected_code
    assert decision.passed is (expected_code == 0)


@pytest.mark.parametrize("value", ["nan", "inf", "100.01", "-0.1", "100.1"])
def test_target_los_rejects_degenerate_values(value):
    with pytest.raises(Exception):
        los_gate.parse_target_los(value)
