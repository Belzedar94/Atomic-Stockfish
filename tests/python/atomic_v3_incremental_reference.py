#!/usr/bin/env python3
"""Independent HM-incremental execution oracle for AtomicNNUEV3.

This module models the first, deliberately narrow H9.3i step.  HM rows are
updated from immutable position snapshots with exact remove-before-add
arithmetic.  CapturePair, KingBlastEP and BlastRing are independently refreshed
from every snapshot; no piece-delta or legal-move implementation is shared with
the engine.  Every accepted frame is compared with the H9.3h scalar
full-refresh reference before it is published.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

import atomic_v3_blast_ring_reference as blast_ring
import atomic_v3_capture_pair_reference as capture_pair
import atomic_v3_full_refresh_reference as full_refresh
import atomic_v3_king_blast_ep_reference as king_blast_ep
import atomic_v3_scalar_reference as scalar


INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1
INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1


class IncrementalOracleError(ValueError):
    """A snapshot transition could not be published transactionally."""


@dataclass(frozen=True)
class HmPerspectiveState:
    perspective: str
    orientation: capture_pair.Orientation
    rows: Tuple[int, ...]
    accumulator: Tuple[int, ...]
    psqt: Tuple[int, ...]


@dataclass(frozen=True)
class HmTransition:
    state: HmPerspectiveState
    removed: Tuple[int, ...]
    added: Tuple[int, ...]
    retained: Tuple[int, ...]
    rebuilt: bool

    @property
    def reused(self) -> bool:
        return not self.rebuilt and not self.removed and not self.added


@dataclass(frozen=True)
class IncrementalFrame:
    position: capture_pair.CapturePosition
    white: HmTransition
    black: HmTransition
    result: Mapping[str, object]

    def transition(self, perspective: str) -> HmTransition:
        if perspective == capture_pair.WHITE:
            return self.white
        if perspective == capture_pair.BLACK:
            return self.black
        raise IncrementalOracleError("perspective must be WHITE or BLACK")


def _checked_i32(value: int, context: str) -> int:
    if not INT32_MIN <= value <= INT32_MAX:
        raise IncrementalOracleError(f"{context} escaped i32: {value}")
    return value


def _checked_i64(value: int, context: str) -> int:
    if not INT64_MIN <= value <= INT64_MAX:
        raise IncrementalOracleError(f"{context} escaped i64: {value}")
    return value


def _canonical_rows(
    rows: Iterable[int], minimum: int, maximum: int, context: str
) -> Tuple[int, ...]:
    materialized = tuple(rows)
    if any(isinstance(row, bool) or not isinstance(row, int) for row in materialized):
        raise IncrementalOracleError(f"{context} rows must be integers")
    ordered = tuple(sorted(materialized))
    if len(ordered) != len(set(ordered)):
        raise IncrementalOracleError(f"{context} contains a duplicate row")
    if any(not minimum <= row < maximum for row in ordered):
        raise IncrementalOracleError(f"{context} contains an out-of-range row")
    return ordered


def canonical_hm_rows(emission: full_refresh.FullRefreshEmission) -> Tuple[int, ...]:
    """Return one perspective's sorted-unique physical HM row state."""

    return _canonical_rows(
        (activation.physical_index for activation in emission.hm),
        scalar.HM_PHYSICAL_OFFSET,
        capture_pair.PHYSICAL_OFFSET,
        "HM",
    )


def merge_row_difference(
    old_rows: Sequence[int], new_rows: Sequence[int]
) -> Tuple[Tuple[int, ...], Tuple[int, ...], Tuple[int, ...]]:
    """Compute old-only, new-only and shared rows without using set arithmetic."""

    old = _canonical_rows(
        old_rows,
        scalar.HM_PHYSICAL_OFFSET,
        capture_pair.PHYSICAL_OFFSET,
        "old HM",
    )
    new = _canonical_rows(
        new_rows,
        scalar.HM_PHYSICAL_OFFSET,
        capture_pair.PHYSICAL_OFFSET,
        "new HM",
    )
    removed = []
    added = []
    retained = []
    old_index = 0
    new_index = 0
    while old_index < len(old) and new_index < len(new):
        old_row = old[old_index]
        new_row = new[new_index]
        if old_row < new_row:
            removed.append(old_row)
            old_index += 1
        elif new_row < old_row:
            added.append(new_row)
            new_index += 1
        else:
            retained.append(old_row)
            old_index += 1
            new_index += 1
    removed.extend(old[old_index:])
    added.extend(new[new_index:])
    return tuple(removed), tuple(added), tuple(retained)


def _apply_sparse_row(
    accumulator: list[int],
    weights: scalar.SparseRows,
    local_row: int,
    sign: int,
    context: str,
) -> None:
    if sign not in (-1, 1):
        raise IncrementalOracleError(f"{context} sign must be -1 or 1")
    for output, value in weights.get(local_row, ()):
        if not 0 <= output < scalar.ACCUMULATOR_DIMENSIONS:
            raise IncrementalOracleError(f"{context} output escaped the accumulator")
        accumulator[output] += sign * value


def _apply_hm_row(
    accumulator: list[int],
    psqt: list[int],
    network: scalar.SparseNetwork,
    physical_row: int,
    sign: int,
) -> None:
    local_row = physical_row - scalar.HM_PHYSICAL_OFFSET
    if not 0 <= local_row < capture_pair.PHYSICAL_OFFSET:
        raise IncrementalOracleError("HM physical row escaped its slice")
    _apply_sparse_row(accumulator, network.hm, local_row, sign, "HM")
    for bucket, value in network.hm_psqt.get(local_row, ()):
        if not 0 <= bucket < scalar.PSQT_BUCKETS:
            raise IncrementalOracleError("HM PSQT bucket escaped its tensor")
        psqt[bucket] = _checked_i64(
            psqt[bucket] + sign * value, "HM PSQT accumulator"
        )


def _validate_hm_state(state: HmPerspectiveState) -> None:
    if state.perspective not in capture_pair.COLORS:
        raise IncrementalOracleError("HM state has an invalid perspective")
    if state.orientation.perspective != state.perspective:
        raise IncrementalOracleError("HM state mixed perspective and orientation")
    if state.rows != _canonical_rows(
        state.rows,
        scalar.HM_PHYSICAL_OFFSET,
        capture_pair.PHYSICAL_OFFSET,
        "HM state",
    ):
        raise IncrementalOracleError("HM state rows are not sorted")
    if len(state.accumulator) != scalar.ACCUMULATOR_DIMENSIONS:
        raise IncrementalOracleError("HM accumulator has the wrong width")
    if len(state.psqt) != scalar.PSQT_BUCKETS:
        raise IncrementalOracleError("HM PSQT state has the wrong width")
    for value in state.accumulator:
        if not scalar.FEATURE_ACCUMULATOR_MINIMUM <= value <= scalar.FEATURE_ACCUMULATOR_MAXIMUM:
            raise IncrementalOracleError("HM accumulator escaped the proved V3 envelope")
    for value in state.psqt:
        _checked_i64(value, "HM PSQT accumulator")
        if not INT32_MIN <= value <= INT32_MAX:
            raise IncrementalOracleError(
                "HM PSQT accumulator escaped the publishable i32 envelope"
            )


def transition_hm(
    network: scalar.SparseNetwork,
    emission: full_refresh.FullRefreshEmission,
    perspective: str,
    previous: Optional[HmPerspectiveState],
) -> HmTransition:
    """Build or update one HM perspective with exact Python integer arithmetic."""

    if perspective not in capture_pair.COLORS:
        raise IncrementalOracleError("perspective must be WHITE or BLACK")
    if previous is not None:
        _validate_hm_state(previous)
        if previous.perspective != perspective:
            raise IncrementalOracleError("HM source perspective is inconsistent")
    rows = canonical_hm_rows(emission)
    orientation = emission.orientation
    rebuild = previous is None or previous.orientation != orientation

    if rebuild:
        accumulator = list(network.biases)
        if len(accumulator) != scalar.ACCUMULATOR_DIMENSIONS:
            raise IncrementalOracleError("network biases have the wrong width")
        psqt = [0] * scalar.PSQT_BUCKETS
        removed = () if previous is None else previous.rows
        added = rows
        retained: Tuple[int, ...] = ()
        for row in rows:
            _apply_hm_row(accumulator, psqt, network, row, 1)
    else:
        if previous is None:
            raise AssertionError("non-rebuild transition lost its source state")
        removed, added, retained = merge_row_difference(previous.rows, rows)
        accumulator = list(previous.accumulator)
        psqt = list(previous.psqt)
        # Removal precedes addition exactly, including when a move reuses rows.
        for row in removed:
            _apply_hm_row(accumulator, psqt, network, row, -1)
        for row in added:
            _apply_hm_row(accumulator, psqt, network, row, 1)

    state = HmPerspectiveState(
        perspective,
        orientation,
        rows,
        tuple(accumulator),
        tuple(psqt),
    )
    _validate_hm_state(state)
    return HmTransition(state, removed, added, retained, rebuild)


def _add_relation_rows(
    accumulator: list[int],
    physical_rows: Iterable[int],
    physical_offset: int,
    physical_dimensions: int,
    weights: scalar.SparseRows,
    context: str,
) -> Tuple[int, ...]:
    rows = _canonical_rows(
        physical_rows,
        physical_offset,
        physical_offset + physical_dimensions,
        context,
    )
    for physical_row in rows:
        _apply_sparse_row(
            accumulator,
            weights,
            physical_row - physical_offset,
            1,
            context,
        )
    return rows


def _compose_perspective(
    network: scalar.SparseNetwork,
    emission: full_refresh.FullRefreshEmission,
    hm_state: HmPerspectiveState,
) -> Tuple[Mapping[str, Tuple[int, ...]], Tuple[int, ...], Tuple[int, ...]]:
    accumulator = list(hm_state.accumulator)
    indices = emission.physical_indices()
    _add_relation_rows(
        accumulator,
        indices["capture_pair"],
        scalar.CAPTURE_PAIR_PHYSICAL_OFFSET,
        capture_pair.PHYSICAL_DIMENSIONS,
        network.capture_pair,
        "CapturePair",
    )
    _add_relation_rows(
        accumulator,
        indices["king_blast_ep"],
        scalar.KING_BLAST_EP_PHYSICAL_OFFSET,
        king_blast_ep.PHYSICAL_DIMENSIONS,
        network.king_blast_ep,
        "KingBlastEP",
    )
    _add_relation_rows(
        accumulator,
        indices["blast_ring"],
        scalar.BLAST_RING_PHYSICAL_OFFSET,
        blast_ring.PHYSICAL_DIMENSIONS,
        network.blast_ring,
        "BlastRing",
    )
    for value in accumulator:
        if not scalar.FEATURE_ACCUMULATOR_MINIMUM <= value <= scalar.FEATURE_ACCUMULATOR_MAXIMUM:
            raise IncrementalOracleError("full accumulator escaped the proved V3 envelope")
    rows = MappingProxyType(
        {
            # H9.3h exposes semantic emission order in its diagnostic rows.
            # The private incremental HM state remains sorted-unique, while
            # the public comparison surface preserves that independent order.
            "hm": indices["hm"],
            "capture_pair": indices["capture_pair"],
            "king_blast_ep": indices["king_blast_ep"],
            "blast_ring": indices["blast_ring"],
        }
    )
    return rows, tuple(accumulator), hm_state.psqt


def _transform_half(accumulator: Sequence[int]) -> Tuple[int, ...]:
    if len(accumulator) != scalar.ACCUMULATOR_DIMENSIONS:
        raise IncrementalOracleError("transform received an invalid accumulator width")
    return tuple(
        min(255, max(0, accumulator[index]))
        * min(255, max(0, accumulator[index + 512]))
        // 512
        for index in range(512)
    )


def _compose_result(
    network: scalar.SparseNetwork,
    position: capture_pair.CapturePosition,
    emissions: Mapping[str, full_refresh.FullRefreshEmission],
    transitions: Mapping[str, HmTransition],
) -> Mapping[str, object]:
    by_color = {
        perspective: _compose_perspective(
            network, emissions[perspective], transitions[perspective].state
        )
        for perspective in capture_pair.COLORS
    }
    white_bucket = emissions[capture_pair.WHITE].network_bucket
    black_bucket = emissions[capture_pair.BLACK].network_bucket
    if white_bucket != black_bucket or not 0 <= white_bucket < scalar.LAYER_STACKS:
        raise IncrementalOracleError("perspectives selected inconsistent dense buckets")
    bucket = white_bucket
    opponent = (
        capture_pair.BLACK
        if position.side_to_move == capture_pair.WHITE
        else capture_pair.WHITE
    )
    stm_state = by_color[position.side_to_move]
    opponent_state = by_color[opponent]
    transformed = _transform_half(stm_state[1]) + _transform_half(opponent_state[1])
    psqt_difference = _checked_i32(
        scalar.trunc_div(stm_state[2][bucket] - opponent_state[2][bucket], 2),
        "PSQT perspective difference",
    )

    result: Dict[str, object] = {
        "side_to_move": 0 if position.side_to_move == capture_pair.WHITE else 1,
        "network_bucket": bucket,
        "transformed": transformed,
        "psqt_difference": psqt_difference,
        "psqt_value": scalar.trunc_div(psqt_difference, scalar.OUTPUT_SCALE),
    }
    result.update(scalar.propagate_dense(transformed, network.dense[bucket]))
    for perspective, prefix in (
        (capture_pair.WHITE, "white"),
        (capture_pair.BLACK, "black"),
    ):
        rows, accumulator, psqt = by_color[perspective]
        result[f"{prefix}.hm"] = rows["hm"]
        result[f"{prefix}.capture_pair"] = rows["capture_pair"]
        result[f"{prefix}.king_blast_ep"] = rows["king_blast_ep"]
        result[f"{prefix}.blast_ring"] = rows["blast_ring"]
        result[f"{prefix}.accumulator"] = accumulator
        result[f"{prefix}.psqt"] = psqt
    return MappingProxyType(result)


def _first_difference(actual: object, expected: object) -> str:
    if isinstance(actual, Sequence) and isinstance(expected, Sequence):
        if len(actual) != len(expected):
            return f"length {len(actual)} != {len(expected)}"
        for index, (actual_value, expected_value) in enumerate(zip(actual, expected)):
            if actual_value != expected_value:
                return f"index {index}: {actual_value!r} != {expected_value!r}"
    return f"{actual!r} != {expected!r}"


def _require_full_refresh_equality(
    actual: Mapping[str, object], expected: Mapping[str, object]
) -> None:
    if set(actual) != set(expected):
        raise IncrementalOracleError(
            "incremental/full-refresh keys differ: "
            f"actual={sorted(actual)} expected={sorted(expected)}"
        )
    for key, expected_value in expected.items():
        actual_value = actual[key]
        if actual_value != expected_value:
            raise IncrementalOracleError(
                f"incremental/full-refresh {key} differs at "
                f"{_first_difference(actual_value, expected_value)}"
            )


def _validate_position(position: capture_pair.CapturePosition) -> None:
    if not isinstance(position, capture_pair.CapturePosition):
        raise IncrementalOracleError("snapshot must be a CapturePosition")
    # Revalidate frozen dataclass instances so deliberately malformed test
    # snapshots cannot bypass the exactly-one-king/material contract.
    capture_pair.validate_material(position.pieces)
    if position.side_to_move not in capture_pair.COLORS:
        raise IncrementalOracleError("snapshot side_to_move must be WHITE or BLACK")


def _build_frame(
    network: scalar.SparseNetwork,
    position: capture_pair.CapturePosition,
    previous: Optional[Mapping[str, HmPerspectiveState]],
) -> Tuple[IncrementalFrame, Mapping[str, HmPerspectiveState]]:
    _validate_position(position)
    emissions = {
        perspective: full_refresh.enumerate_full_refresh(position, perspective)
        for perspective in capture_pair.COLORS
    }
    transitions = {
        perspective: transition_hm(
            network,
            emissions[perspective],
            perspective,
            None if previous is None else previous[perspective],
        )
        for perspective in capture_pair.COLORS
    }
    result = _compose_result(network, position, emissions, transitions)
    expected = scalar.evaluate(network, position)
    _require_full_refresh_equality(result, expected)
    frame = IncrementalFrame(
        position,
        transitions[capture_pair.WHITE],
        transitions[capture_pair.BLACK],
        result,
    )
    states = MappingProxyType(
        {
            perspective: transitions[perspective].state
            for perspective in capture_pair.COLORS
        }
    )
    return frame, states


class IncrementalOracle:
    """Transactional snapshot-sequence evaluator for private H9.3i tests."""

    def __init__(self, network: scalar.SparseNetwork):
        if not isinstance(network, scalar.SparseNetwork):
            raise IncrementalOracleError("network must be a SparseNetwork")
        self._network = network
        self._states: Optional[Mapping[str, HmPerspectiveState]] = None
        self._last_frame: Optional[IncrementalFrame] = None
        self._accepted_snapshots = 0

    @property
    def states(self) -> Optional[Mapping[str, HmPerspectiveState]]:
        return self._states

    @property
    def last_frame(self) -> Optional[IncrementalFrame]:
        return self._last_frame

    @property
    def accepted_snapshots(self) -> int:
        return self._accepted_snapshots

    def advance(self, position: capture_pair.CapturePosition) -> IncrementalFrame:
        return self.advance_many((position,))[0]

    def advance_many(
        self, positions: Sequence[capture_pair.CapturePosition]
    ) -> Tuple[IncrementalFrame, ...]:
        """Evaluate captured lazy frames and publish only if the whole chain succeeds."""

        if not isinstance(positions, Sequence) or isinstance(positions, (str, bytes)):
            raise IncrementalOracleError("snapshot chain must be a sequence")
        if not positions:
            return ()

        candidate_states = self._states
        candidate_frames = []
        try:
            for position in positions:
                frame, candidate_states = _build_frame(
                    self._network, position, candidate_states
                )
                candidate_frames.append(frame)
        except IncrementalOracleError:
            raise
        except (AssertionError, KeyError, StopIteration, TypeError, ValueError) as error:
            raise IncrementalOracleError(
                f"snapshot chain rejected before publication: {error}"
            ) from error

        frames = tuple(candidate_frames)
        self._states = candidate_states
        self._last_frame = frames[-1]
        self._accepted_snapshots += len(frames)
        return frames


__all__ = (
    "HmPerspectiveState",
    "HmTransition",
    "IncrementalFrame",
    "IncrementalOracle",
    "IncrementalOracleError",
    "canonical_hm_rows",
    "merge_row_difference",
    "transition_hm",
)
