from __future__ import annotations

import sys
from pathlib import Path

import pytest


TESTS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TESTS_ROOT))

from atomic_nnue_differential import (  # noqa: E402
    CorpusPosition,
    EvalResult,
    compare_corpus,
)


class FixedEvaluator:
    def __init__(self, result: EvalResult) -> None:
        self.result = result

    def evaluate(self, _position: CorpusPosition) -> EvalResult:
        return self.result


def result(final: float, raw: int | None, mode: str | None) -> EvalResult:
    return EvalResult(
        final_white=final,
        raw_white=1.0 if mode is None else None,
        raw_internal_stm=raw,
        mode=mode,
    )


def compare(
    halfmove: int, candidate_final: float, oracle_final: float
) -> tuple[float, float, float, int]:
    position = CorpusPosition(
        fen=f"8/8/8/8/8/8/1K6/7k w - - {halfmove} 1",
        chess960=False,
        source="unit",
    )
    return compare_corpus(
        [position],
        candidate_true=FixedEvaluator(result(candidate_final, 208, "true")),
        candidate_pure=FixedEvaluator(result(1.0, 208, "pure")),
        oracle=FixedEvaluator(result(oracle_final, None, None)),
        true_tolerance=0.1,
        pure_tolerance=0.1,
        max_errors=1,
        progress=0,
    )


def test_rule50_boundary_requires_candidate_zero_but_not_fairy_parity() -> None:
    max_true, max_rule50, max_pure, damped = compare(100, 0.0, -0.74)
    assert max_true == pytest.approx(0.0)
    assert max_rule50 == pytest.approx(0.74)
    assert max_pure == pytest.approx(0.0)
    assert damped == 1


def test_rule50_boundary_rejects_nonzero_candidate() -> None:
    with pytest.raises(AssertionError, match="trace is not neutral"):
        compare(108, 0.01, -0.74)


def test_before_rule50_boundary_still_requires_oracle_sanity_bound() -> None:
    with pytest.raises(AssertionError, match="true final differs"):
        compare(99, 0.0, -0.74)
