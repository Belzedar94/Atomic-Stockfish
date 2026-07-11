#!/usr/bin/env python3
"""Cooperative, pair-safe LOS gate for variantfishtest_new1.py.

The match runner remains an external, unmodified source of truth.  This wrapper
imports it by file path, subclasses ``EngineMatch`` only to extend ``stop()``,
and obtains the displayed LOS from the runner's own ``elo_stats()`` function.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import importlib.util
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Sequence


LOS_RE = re.compile(r"(?:^|\s)LOS:\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))%")


class GateConfigurationError(ValueError):
    """Raised when a match cannot preserve the Atomic LOS gate contract."""


@dataclass(frozen=True)
class GateConfig:
    min_total_exclusive: int
    target_los_display: str


@dataclass(frozen=True)
class GateDecision:
    total: int
    displayed_los: str | None
    complete_pairs: bool
    passed: bool


def parse_target_los(value: str) -> str:
    """Parse a finite percentage which is exactly representable at one decimal."""

    try:
        target = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("target LOS must be a number") from exc

    if not target.is_finite() or target < 0 or target > 100:
        raise argparse.ArgumentTypeError("target LOS must be between 0.0 and 100.0")

    one_decimal = target.quantize(Decimal("0.1"))
    if target != one_decimal:
        raise argparse.ArgumentTypeError(
            "target LOS must have no precision beyond the runner's one decimal"
        )
    return format(one_decimal, ".1f")


def load_runner_module(runner_path: Path) -> ModuleType:
    """Import the external runner from ``runner_path`` without editing it."""

    path = runner_path.expanduser().resolve()
    if not path.is_file():
        raise GateConfigurationError(f"runner does not exist: {path}")

    module_name = f"_atomic_variantfishtest_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise GateConfigurationError(f"cannot import runner: {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    finally:
        sys.path.pop(0)

    if not hasattr(module, "EngineMatch") or not callable(
        getattr(module, "elo_stats", None)
    ):
        raise GateConfigurationError(
            "runner must expose EngineMatch and elo_stats(scores)"
        )

    # A sibling stat_util.py is part of this runner's statistical definition.
    # Refuse an accidentally preloaded module from another installation.
    expected_stat_util = path.parent / "stat_util.py"
    stat_util = getattr(module, "stat_util", None)
    loaded_stat_util = getattr(stat_util, "__file__", None)
    if expected_stat_util.is_file() and (
        loaded_stat_util is None
        or Path(loaded_stat_util).resolve() != expected_stat_util.resolve()
    ):
        raise GateConfigurationError(
            f"runner did not load its sibling stat_util.py: {expected_stat_util}"
        )

    return module


def runner_displayed_los(runner: ModuleType, scores: Sequence[int]) -> str | None:
    """Return exactly the one-decimal LOS text emitted by the external runner."""

    try:
        output = runner.elo_stats(list(scores))
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    match = LOS_RE.search(output or "")
    return match.group(1) if match else None


def evaluate_gate(
    runner: ModuleType, scores: Sequence[int], config: GateConfig
) -> GateDecision:
    """Evaluate ``Total > threshold`` and exact displayed LOS equality."""

    if len(scores) != 3:
        return GateDecision(0, None, False, False)
    try:
        numeric_scores = [int(score) for score in scores]
    except (TypeError, ValueError, OverflowError):
        return GateDecision(0, None, False, False)
    if any(score < 0 for score in numeric_scores):
        return GateDecision(sum(numeric_scores), None, False, False)

    total = sum(numeric_scores)
    displayed_los = runner_displayed_los(runner, numeric_scores)
    complete_pairs = total % 2 == 0
    passed = (
        complete_pairs
        and total > config.min_total_exclusive
        and displayed_los == config.target_los_display
    )
    return GateDecision(total, displayed_los, complete_pairs, passed)


def _nnue_playing_modes(engine_options: Sequence[dict[str, object]]) -> list[str | None]:
    modes: list[str | None] = []
    for options in engine_options:
        mode = None
        for name, value in options.items():
            if str(name).strip().casefold() == "use nnue":
                mode = str(value).strip().casefold()
                break
        modes.append(mode)
    return modes


def validate_match_configuration(match: object, config: GateConfig) -> None:
    """Reject settings that can truncate a pair or change the LOS contract."""

    max_games = int(getattr(match, "max_games", 0))
    if max_games <= config.min_total_exclusive:
        raise GateConfigurationError(
            "max_games must be greater than the exclusive Total threshold"
        )
    if max_games % 2:
        raise GateConfigurationError(
            "max_games must be even so the runner never truncates a color-swapped pair"
        )
    if bool(getattr(match, "sprt", False)):
        raise GateConfigurationError("SPRT mode is incompatible with the exact LOS gate")
    nnue_modes = _nnue_playing_modes(getattr(match, "engine_options", []))
    if len(nnue_modes) != 2 or any(mode != "true" for mode in nnue_modes):
        raise GateConfigurationError(
            "both engines must set Use NNUE=true explicitly; pure is reserved for data generation"
        )


def make_gated_match_class(runner: ModuleType, config: GateConfig) -> type:
    """Extend the runner's stop condition without changing match execution."""

    class CooperativeGateMatch(runner.EngineMatch):  # type: ignore[misc, name-defined]
        gate_observed = False

        def stop(self) -> bool:
            decision = evaluate_gate(runner, self.scores, config)
            if decision.passed:
                self.gate_observed = True
                return True
            return super().stop()

    CooperativeGateMatch.__name__ = "CooperativeGateMatch"
    return CooperativeGateMatch


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Import variantfishtest_new1.py and stop cooperatively after complete "
            "pairs when its displayed LOS gate passes. Runner arguments follow --."
        )
    )
    parser.add_argument(
        "--runner", required=True, type=Path, help="path to variantfishtest_new1.py"
    )
    parser.add_argument(
        "--min-total-exclusive",
        required=True,
        type=int,
        help="require Total to be strictly greater than this value",
    )
    parser.add_argument(
        "--target-los",
        required=True,
        type=parse_target_los,
        help="exact one-decimal LOS percentage displayed by the runner",
    )
    return parser


def _split_wrapper_and_runner_args(argv: Sequence[str]) -> tuple[list[str], list[str]]:
    try:
        separator = list(argv).index("--")
    except ValueError as exc:
        raise GateConfigurationError(
            "separate wrapper options from variantfishtest arguments with --"
        ) from exc
    wrapper_args = list(argv[:separator])
    runner_args = list(argv[separator + 1 :])
    if not runner_args:
        raise GateConfigurationError("variantfishtest arguments are required after --")
    return wrapper_args, runner_args


def run_gate(
    runner: ModuleType, runner_args: Sequence[str], config: GateConfig
) -> tuple[int, GateDecision]:
    """Run one gated match and return its process code and final decision."""

    match_class = make_gated_match_class(runner, config)
    original_argv = sys.argv
    sys.argv = [str(getattr(runner, "__file__", "variantfishtest_new1.py"))] + list(
        runner_args
    )
    match = None
    try:
        match = match_class()
    finally:
        sys.argv = original_argv

    try:
        validate_match_configuration(match, config)
        match.run()
        decision = evaluate_gate(runner, match.scores, config)
    finally:
        # EngineMatch.run() normally closes this itself.  This also covers a
        # configuration error before threads are launched.
        if match is not None:
            match.close()

    return (0 if decision.passed else 1), decision


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        wrapper_args, runner_args = _split_wrapper_and_runner_args(args)
        namespace = _build_parser().parse_args(wrapper_args)
        if namespace.min_total_exclusive < 0:
            raise GateConfigurationError("exclusive Total threshold cannot be negative")
        config = GateConfig(
            min_total_exclusive=namespace.min_total_exclusive,
            target_los_display=namespace.target_los,
        )
        runner = load_runner_module(namespace.runner)
        code, decision = run_gate(runner, runner_args, config)
    except GateConfigurationError as exc:
        print(f"Atomic LOS gate configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Atomic LOS gate interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Atomic LOS gate runner error: {exc}", file=sys.stderr)
        return 2

    los = decision.displayed_los if decision.displayed_los is not None else "unavailable"
    status = "PASS" if decision.passed else "FAIL"
    print(
        f"Atomic LOS gate: {status} Total: {decision.total} "
        f"LOS: {los}% complete_pairs: {decision.complete_pairs}"
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
