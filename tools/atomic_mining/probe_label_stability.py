"""Summarize repeated teacher-label stability for Atomic mining probes.

The input is canonical JSONL with schema ``atomic-dual-probe-v1``. Structural
wire violations fail closed and produce no sidecar. Semantically incomplete
protocol rows and non-exact score bounds remain reportable evidence, but force
the ``atomic-label-stability-v1`` recommendation to ``KILL``.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Mapping, Sequence

from tools.atomic_mining import common
from tools.atomic_mining.split_components import (
    SplitContractError,
    validate_candidate,
)


INPUT_SCHEMA = "atomic-dual-probe-v1"
OUTPUT_SCHEMA = "atomic-label-stability-v1"
SPLITS = frozenset({"calibration", "confirmation", "technical-smoke"})
SCORE_KINDS = frozenset({"cp", "mate"})
SCORE_BOUNDS = frozenset({"exact", "lower", "upper"})
RATE_TARGET = 0.0
KILL_RATE_LIMIT = 0.01
PROBE_ORDER_POLICY = "position-id-parity-alternating-v1"
_UCI_MOVE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")
_SQUARE = re.compile(r"^[a-h][1-8]$")

_TOP_FIELDS = (
    "schema",
    "split",
    "position_id",
    "component_id",
    "candidate",
    "probe_config",
    "features",
    "current",
    "teacher_repeats",
    "teacher_on_current_move",
)
_CANDIDATE_FIELDS = (
    "schema",
    "variant",
    "scientific_role",
    "outcome",
    "source",
    "game",
    "position",
)
_SOURCE_FIELDS = ("path", "sha256", "size_bytes")
_GAME_FIELDS = (
    "game_index",
    "marker_line",
    "root_fen",
    "root_fen_sha256",
    "source_game_id",
    "full_game_history_sha256",
    "move_count",
)
_POSITION_FIELDS = (
    "root_ply",
    "fen",
    "played_move",
    "legal_move_count",
    "terminal_state",
    "history_uci",
    "full_history_sha256",
    "transposition_key",
    "transposition_key_scope",
    "engine_key",
    "checkers",
)
_PROBE_CONFIG_FIELDS = (
    "engine",
    "current_net",
    "teacher_net",
    "nodes",
    "multipv",
    "threads",
    "hash_mb",
    "clear_hash",
    "teacher_repeat_count",
    "engine_options",
    "probe_order_policy",
)
_ARTIFACT_FIELDS = ("path", "sha256", "size_bytes")
_ENGINE_OPTION_FIELDS = ("name", "value")
_FEATURE_FIELDS = (
    "classifier",
    "in_check",
    "post_capture",
    "post_capture_in_check",
    "teacher_move_capture",
    "teacher_move_gives_check",
    "teacher_move_promotion",
    "forced_chain",
    "primary_stratum",
    "strata",
)
_SNAPSHOT_FIELDS = (
    "bestmove",
    "ponder",
    "principal",
    "multipv",
    "raw_sha256",
    "raw_line_count",
)
_INFO_FIELDS = (
    "depth",
    "seldepth",
    "nodes",
    "multipv",
    "score",
    "wdl",
    "pv",
)
_SCORE_FIELDS = ("kind", "value", "bound")
_INPUT_PROVENANCE_FIELDS = ("path", "sha256", "size_bytes")


class StabilityContractError(common.MiningArtifactError):
    """The dual-probe input or stability output contract is not satisfied."""


@dataclass(frozen=True)
class _ValidatedRow:
    position_id: str
    component_id: str
    split: str
    source_sha256: str
    primary_stratum: str
    probe_config_sha256: str
    current: Mapping[str, object]
    teacher_repeats: tuple[Mapping[str, object], ...]
    teacher_on_current_move: Mapping[str, object] | None


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise StabilityContractError(f"{label} must be an object")
    return value


def _strict_int(
    value: object,
    *,
    label: str,
    minimum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StabilityContractError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise StabilityContractError(f"{label} must be >= {minimum}")
    return value


def _nonempty_string(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise StabilityContractError(f"{label} must be a non-empty string")
    if any(character in value for character in "\r\n\0"):
        raise StabilityContractError(f"{label} contains a control character")
    return value


def _optional_string(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    return _nonempty_string(value, label=label)


def _validate_move(value: object, *, label: str) -> str:
    move = _nonempty_string(value, label=label)
    if _UCI_MOVE.fullmatch(move) is None:
        raise StabilityContractError(f"{label} must be a UCI move")
    return move


def _validate_artifact(value: object, *, label: str) -> Mapping[str, object]:
    artifact = _mapping(value, label=label)
    common.require_exact_fields(artifact, _ARTIFACT_FIELDS, label=label)
    _nonempty_string(artifact["path"], label=f"{label}.path")
    common.require_lower_hex_sha256(
        artifact["sha256"], label=f"{label}.sha256"
    )
    _strict_int(
        artifact["size_bytes"], label=f"{label}.size_bytes", minimum=1
    )
    return artifact


def _validate_candidate_complete(
    value: object,
    *,
    position_id: str,
    split: str,
) -> str:
    candidate = _mapping(value, label="row.candidate")
    try:
        identity = validate_candidate(candidate)
    except SplitContractError as error:
        raise StabilityContractError(str(error)) from error
    if identity.position_id != position_id:
        raise StabilityContractError(
            "row.position_id does not match the complete candidate identity"
        )
    if (
        identity.candidate_schema == "atomic-e00-position-v1"
        and split == "technical-smoke"
    ):
        raise StabilityContractError(
            "result-bearing candidates cannot bypass the five-way split "
            "through technical-smoke"
        )
    return identity.source_group_sha256


def _validate_probe_config(
    value: object,
    *,
    teacher_repeat_count: int,
) -> str:
    config = _mapping(value, label="row.probe_config")
    common.require_exact_fields(
        config, _PROBE_CONFIG_FIELDS, label="row.probe_config"
    )
    for name in ("engine", "current_net", "teacher_net"):
        _validate_artifact(config[name], label=f"row.probe_config.{name}")
    _strict_int(config["nodes"], label="row.probe_config.nodes", minimum=1)
    multipv = _strict_int(
        config["multipv"], label="row.probe_config.multipv", minimum=2
    )
    threads = _strict_int(
        config["threads"], label="row.probe_config.threads", minimum=1
    )
    if threads != 1:
        raise StabilityContractError("row.probe_config.threads must be exactly 1")
    _strict_int(config["hash_mb"], label="row.probe_config.hash_mb", minimum=1)
    if config["clear_hash"] is not True:
        raise StabilityContractError(
            "row.probe_config.clear_hash must be exactly true"
        )
    declared_repeats = _strict_int(
        config["teacher_repeat_count"],
        label="row.probe_config.teacher_repeat_count",
        minimum=2,
    )
    if declared_repeats != teacher_repeat_count:
        raise StabilityContractError(
            "row.probe_config.teacher_repeat_count does not match teacher_repeats"
        )
    expected_options: list[dict[str, object]] = [
        {"name": "UCI_Variant", "value": "atomic"},
        {"name": "Threads", "value": 1},
        {"name": "Hash", "value": config["hash_mb"]},
        {"name": "MultiPV", "value": multipv},
        {"name": "Ponder", "value": False},
        {"name": "SyzygyPath", "value": "<empty>"},
        {"name": "SyzygyProbeLimit", "value": 0},
        {"name": "Use NNUE", "value": "pure"},
    ]
    engine_options = config["engine_options"]
    if not isinstance(engine_options, list):
        raise StabilityContractError(
            "row.probe_config.engine_options must be an array"
        )
    for index, option_value in enumerate(engine_options):
        option = _mapping(
            option_value, label=f"row.probe_config.engine_options[{index}]"
        )
        common.require_exact_fields(
            option,
            _ENGINE_OPTION_FIELDS,
            label=f"row.probe_config.engine_options[{index}]",
        )
        _nonempty_string(
            option["name"],
            label=f"row.probe_config.engine_options[{index}].name",
        )
    if engine_options != expected_options:
        raise StabilityContractError(
            "row.probe_config.engine_options must equal the exact ordered "
            "Atomic mining option contract"
        )
    if config["probe_order_policy"] != PROBE_ORDER_POLICY:
        raise StabilityContractError(
            "row.probe_config.probe_order_policy must be "
            f"{PROBE_ORDER_POLICY!r}"
        )
    return common.sha256_bytes(common.canonical_json_bytes(config))


def _validate_features(value: object) -> str:
    features = _mapping(value, label="row.features")
    common.require_exact_fields(features, _FEATURE_FIELDS, label="row.features")
    _nonempty_string(features["classifier"], label="row.features.classifier")
    for name in (
        "in_check",
        "post_capture",
        "post_capture_in_check",
        "teacher_move_capture",
        "teacher_move_gives_check",
        "teacher_move_promotion",
        "forced_chain",
    ):
        if type(features[name]) is not bool:
            raise StabilityContractError(f"row.features.{name} must be boolean")
    primary = _nonempty_string(
        features["primary_stratum"], label="row.features.primary_stratum"
    )
    strata = features["strata"]
    if not isinstance(strata, list) or not strata:
        raise StabilityContractError("row.features.strata must be a non-empty array")
    normalized: list[str] = []
    for index, item in enumerate(strata):
        normalized.append(
            _nonempty_string(item, label=f"row.features.strata[{index}]")
        )
    if len(set(normalized)) != len(normalized):
        raise StabilityContractError("row.features.strata contains duplicates")
    if primary not in normalized:
        raise StabilityContractError(
            "row.features.primary_stratum must occur in strata"
        )
    return primary


def _validate_score(value: object, *, label: str) -> Mapping[str, object]:
    score = _mapping(value, label=label)
    common.require_exact_fields(score, _SCORE_FIELDS, label=label)
    if score["kind"] not in SCORE_KINDS:
        raise StabilityContractError(f"{label}.kind is invalid")
    _strict_int(score["value"], label=f"{label}.value")
    if score["bound"] not in SCORE_BOUNDS:
        raise StabilityContractError(f"{label}.bound is invalid")
    return score


def _validate_info(value: object, *, label: str) -> Mapping[str, object]:
    info = _mapping(value, label=label)
    common.require_exact_fields(info, _INFO_FIELDS, label=label)
    for name in ("depth", "seldepth", "nodes"):
        _strict_int(info[name], label=f"{label}.{name}", minimum=0)
    _strict_int(info["multipv"], label=f"{label}.multipv", minimum=1)
    _validate_score(info["score"], label=f"{label}.score")
    wdl = info["wdl"]
    if wdl is not None:
        if not isinstance(wdl, list) or len(wdl) != 3:
            raise StabilityContractError(f"{label}.wdl must be null or [w,d,l]")
        values = [
            _strict_int(item, label=f"{label}.wdl[{index}]", minimum=0)
            for index, item in enumerate(wdl)
        ]
        if sum(values) <= 0:
            raise StabilityContractError(f"{label}.wdl must have positive mass")
    pv = info["pv"]
    if not isinstance(pv, list):
        raise StabilityContractError(f"{label}.pv must be an array")
    for index, move in enumerate(pv):
        _validate_move(move, label=f"{label}.pv[{index}]")
    return info


def _validate_snapshot(
    value: object,
    *,
    label: str,
) -> Mapping[str, object]:
    snapshot = _mapping(value, label=label)
    common.require_exact_fields(snapshot, _SNAPSHOT_FIELDS, label=label)
    _optional_string(snapshot["bestmove"], label=f"{label}.bestmove")
    _optional_string(snapshot["ponder"], label=f"{label}.ponder")
    principal = snapshot["principal"]
    if principal is not None:
        _validate_info(principal, label=f"{label}.principal")
    multipv = snapshot["multipv"]
    if not isinstance(multipv, list):
        raise StabilityContractError(f"{label}.multipv must be an array")
    for index, info in enumerate(multipv):
        _validate_info(info, label=f"{label}.multipv[{index}]")
    common.require_lower_hex_sha256(
        snapshot["raw_sha256"], label=f"{label}.raw_sha256"
    )
    _strict_int(
        snapshot["raw_line_count"], label=f"{label}.raw_line_count", minimum=0
    )
    return snapshot


def _validate_row(value: Mapping[str, object]) -> _ValidatedRow:
    common.require_exact_fields(value, _TOP_FIELDS, label="dual-probe row")
    if value["schema"] != INPUT_SCHEMA:
        raise StabilityContractError(f"row.schema must be {INPUT_SCHEMA}")
    split = value["split"]
    if split not in SPLITS:
        raise StabilityContractError("row.split is invalid")
    assert isinstance(split, str)
    position_id = common.require_lower_hex_sha256(
        value["position_id"], label="row.position_id"
    )
    component_id = common.require_lower_hex_sha256(
        value["component_id"], label="row.component_id"
    )
    source_sha256 = _validate_candidate_complete(
        value["candidate"], position_id=position_id, split=split
    )
    primary_stratum = _validate_features(value["features"])

    repeats_raw = value["teacher_repeats"]
    if not isinstance(repeats_raw, list) or len(repeats_raw) < 2:
        raise StabilityContractError(
            "row.teacher_repeats must contain at least two snapshots"
        )
    teacher_repeats = tuple(
        _validate_snapshot(item, label=f"row.teacher_repeats[{index}]")
        for index, item in enumerate(repeats_raw)
    )
    probe_config_sha256 = _validate_probe_config(
        value["probe_config"], teacher_repeat_count=len(teacher_repeats)
    )
    current = _validate_snapshot(value["current"], label="row.current")
    on_current_raw = value["teacher_on_current_move"]
    teacher_on_current_move = (
        None
        if on_current_raw is None
        else _validate_snapshot(
            on_current_raw, label="row.teacher_on_current_move"
        )
    )
    return _ValidatedRow(
        position_id=position_id,
        component_id=component_id,
        split=split,
        source_sha256=source_sha256,
        primary_stratum=primary_stratum,
        probe_config_sha256=probe_config_sha256,
        current=current,
        teacher_repeats=teacher_repeats,
        teacher_on_current_move=teacher_on_current_move,
    )


def _snapshot_protocol_errors(
    snapshot: Mapping[str, object],
    *,
    role: str,
    expected_multipv: int | None,
) -> set[str]:
    errors: set[str] = set()
    bestmove = snapshot["bestmove"]
    if not isinstance(bestmove, str) or _UCI_MOVE.fullmatch(bestmove) is None:
        errors.add(f"{role}:missing-or-invalid-bestmove")
    ponder = snapshot["ponder"]
    if ponder is not None and (
        not isinstance(ponder, str) or _UCI_MOVE.fullmatch(ponder) is None
    ):
        errors.add(f"{role}:invalid-ponder")
    if snapshot["raw_line_count"] == 0:
        errors.add(f"{role}:empty-raw-transcript")

    multipv = snapshot["multipv"]
    assert isinstance(multipv, list)
    lanes: dict[int, Mapping[str, object]] = {}
    for info_value in multipv:
        info = _mapping(info_value, label=f"{role}.multipv")
        lane = info["multipv"]
        assert isinstance(lane, int) and not isinstance(lane, bool)
        if lane in lanes:
            errors.add(f"{role}:duplicate-multipv-lane")
        lanes[lane] = info
    if 1 not in lanes:
        errors.add(f"{role}:missing-principal-lane")
    if lanes and set(lanes) != set(range(1, max(lanes) + 1)):
        errors.add(f"{role}:incomplete-multipv")
    if expected_multipv is not None and any(
        lane > expected_multipv for lane in lanes
    ):
        errors.add(f"{role}:multipv-lane-above-config")

    principal_value = snapshot["principal"]
    if principal_value is None:
        errors.add(f"{role}:missing-principal")
        principal: Mapping[str, object] | None = None
    else:
        principal = _mapping(principal_value, label=f"{role}.principal")
        if lanes.get(1) != principal:
            errors.add(f"{role}:principal-lane-mismatch")
    if principal is not None:
        pv = principal["pv"]
        assert isinstance(pv, list)
        if not pv:
            errors.add(f"{role}:empty-principal-pv")
        elif isinstance(bestmove, str) and pv[0] != bestmove:
            errors.add(f"{role}:bestmove-pv-mismatch")
    return errors


def _snapshot_has_non_exact(snapshot: Mapping[str, object]) -> bool:
    infos: list[Mapping[str, object]] = []
    principal = snapshot["principal"]
    if isinstance(principal, Mapping):
        infos.append(principal)
    multipv = snapshot["multipv"]
    assert isinstance(multipv, list)
    infos.extend(_mapping(value, label="snapshot.multipv") for value in multipv)
    return any(
        _mapping(info["score"], label="snapshot.score")["bound"] != "exact"
        for info in infos
    )


def _principal(snapshot: Mapping[str, object]) -> Mapping[str, object]:
    principal = snapshot["principal"]
    assert isinstance(principal, Mapping)
    return principal


def _score_sign(value: int) -> int:
    return (value > 0) - (value < 0)


def _wdl_expectation(info: Mapping[str, object]) -> Fraction | None:
    wdl = info["wdl"]
    if wdl is None:
        return None
    assert isinstance(wdl, list)
    wins, draws, losses = wdl
    assert all(isinstance(value, int) and not isinstance(value, bool) for value in wdl)
    total = wins + draws + losses
    return Fraction(wins - losses, total)


def _nearest_rank(values: Sequence[int | Fraction], fraction: float) -> int | Fraction | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def _fraction_number(value: int | Fraction | None) -> int | float | None:
    if value is None or isinstance(value, int):
        return value
    return round(float(value), 12)


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 12)


def _count_map(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _validate_input_provenance(
    value: Mapping[str, object],
) -> dict[str, object]:
    common.require_exact_fields(
        value, _INPUT_PROVENANCE_FIELDS, label="input provenance"
    )
    path = value["path"]
    if path is not None:
        _nonempty_string(path, label="input provenance.path")
    sha256 = common.require_lower_hex_sha256(
        value["sha256"], label="input provenance.sha256"
    )
    size_bytes = _strict_int(
        value["size_bytes"], label="input provenance.size_bytes", minimum=0
    )
    return {"path": path, "sha256": sha256, "size_bytes": size_bytes}


def analyze_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    input_provenance: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build one deterministic stability report without reading or writing files."""

    canonical_payload = b"".join(common.canonical_json_bytes(row) for row in rows)
    try:
        provenance = _validate_input_provenance(
            input_provenance
            if input_provenance is not None
            else {
                "path": None,
                "sha256": common.sha256_bytes(canonical_payload),
                "size_bytes": len(canonical_payload),
            }
        )
    except common.MiningArtifactError as error:
        if isinstance(error, StabilityContractError):
            raise
        raise StabilityContractError(str(error)) from error

    validated: list[_ValidatedRow] = []
    seen_positions: set[str] = set()
    for row_number, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise StabilityContractError(f"row {row_number} must be an object")
        try:
            result = _validate_row(row)
        except common.MiningArtifactError as error:
            if isinstance(error, StabilityContractError):
                raise
            raise StabilityContractError(str(error)) from error
        if result.position_id in seen_positions:
            raise StabilityContractError(
                f"duplicate row.position_id: {result.position_id}"
            )
        seen_positions.add(result.position_id)
        validated.append(result)

    total = len(validated)
    config_counts = Counter(row.probe_config_sha256 for row in validated)
    mixed_configs = len(config_counts) > 1
    split_counts = Counter(row.split for row in validated)
    stratum_counts = Counter(row.primary_stratum for row in validated)
    component_counts = Counter(row.component_id for row in validated)
    source_counts = Counter(row.source_sha256 for row in validated)

    protocol_error_counts: Counter[str] = Counter()
    protocol_error_rows = 0
    non_exact_rows = 0
    exact_rows: list[_ValidatedRow] = []
    expected_multipv_by_config: dict[str, int] = {}
    config_documents: dict[str, object] = {}
    for row, original in zip(validated, rows, strict=True):
        config = _mapping(original["probe_config"], label="row.probe_config")
        expected_multipv_by_config[row.probe_config_sha256] = int(config["multipv"])
        config_documents[row.probe_config_sha256] = json.loads(
            common.canonical_json_bytes(config)
        )

    for row in validated:
        errors: set[str] = set()
        expected_multipv = expected_multipv_by_config[row.probe_config_sha256]
        errors.update(
            _snapshot_protocol_errors(
                row.current,
                role="current",
                expected_multipv=expected_multipv,
            )
        )
        for snapshot in row.teacher_repeats:
            errors.update(
                _snapshot_protocol_errors(
                    snapshot,
                    role="teacher-repeat",
                    expected_multipv=expected_multipv,
                )
            )
        if row.teacher_on_current_move is not None:
            errors.update(
                _snapshot_protocol_errors(
                    row.teacher_on_current_move,
                    role="teacher-on-current-move",
                    expected_multipv=None,
                )
            )
        if mixed_configs:
            errors.add("row:mixed-probe-config")
        if errors:
            protocol_error_rows += 1
            protocol_error_counts.update(errors)

        snapshots = [row.current, *row.teacher_repeats]
        if row.teacher_on_current_move is not None:
            snapshots.append(row.teacher_on_current_move)
        non_exact = any(_snapshot_has_non_exact(snapshot) for snapshot in snapshots)
        if non_exact:
            non_exact_rows += 1
        if not errors and not non_exact:
            exact_rows.append(row)

    bestmove_flip_rows = 0
    kind_flip_rows = 0
    sign_flip_rows = 0
    cp_spreads: list[int] = []
    mate_spreads: list[int] = []
    wdl_spreads: list[Fraction] = []
    wdl_complete_rows = 0

    for row in exact_rows:
        bestmoves = [snapshot["bestmove"] for snapshot in row.teacher_repeats]
        principals = [_principal(snapshot) for snapshot in row.teacher_repeats]
        scores = [
            _mapping(info["score"], label="teacher principal score")
            for info in principals
        ]
        kinds = [str(score["kind"]) for score in scores]
        values = [int(score["value"]) for score in scores]
        if len(set(bestmoves)) > 1:
            bestmove_flip_rows += 1
        if len(set(kinds)) > 1:
            kind_flip_rows += 1
        if len({_score_sign(value) for value in values}) > 1:
            sign_flip_rows += 1
        if len(set(kinds)) == 1:
            spread = max(values) - min(values)
            if kinds[0] == "cp":
                cp_spreads.append(spread)
            else:
                mate_spreads.append(spread)

        expectations = [_wdl_expectation(info) for info in principals]
        if all(value is not None for value in expectations):
            concrete = [value for value in expectations if value is not None]
            wdl_spreads.append(max(concrete) - min(concrete))
            wdl_complete_rows += 1

    denominator = len(exact_rows)
    bestmove_rate = _rate(bestmove_flip_rows, denominator)
    kind_rate = _rate(kind_flip_rows, denominator)
    sign_rate = _rate(sign_flip_rows, denominator)

    reasons: list[str] = []
    if total == 0:
        reasons.append("empty-input")
    if protocol_error_rows:
        reasons.append("protocol-error-rows")
    if non_exact_rows:
        reasons.append("non-exact-rows")
    for name, rate in (
        ("bestmove", bestmove_rate),
        ("kind", kind_rate),
        ("sign", sign_rate),
    ):
        if rate is not None and rate > KILL_RATE_LIMIT:
            reasons.append(f"{name}-flip-rate-above-limit")
    if total and not exact_rows:
        reasons.append("no-exact-rows")

    report: dict[str, object] = {
        "schema": OUTPUT_SCHEMA,
        "input": provenance,
        "policy": {
            "teacher_repeat_minimum": 2,
            "rate_denominator": "protocol-clean-exact-rows",
            "target_rate": RATE_TARGET,
            "kill_rate_limit_exclusive": KILL_RATE_LIMIT,
            "score_spread": "per-row-max-minus-min-nearest-rank",
            "wdl_expectation": "(wins-losses)/(wins+draws+losses)",
        },
        "total": total,
        "exact_rows": denominator,
        "protocol_error_rows": protocol_error_rows,
        "protocol_error_counts": _count_map(protocol_error_counts),
        "non_exact_rows": non_exact_rows,
        "bestmove_flip_rows": bestmove_flip_rows,
        "bestmove_flip_rate": bestmove_rate,
        "kind_flip_rows": kind_flip_rows,
        "kind_flip_rate": kind_rate,
        "sign_flip_rows": sign_flip_rows,
        "sign_flip_rate": sign_rate,
        "score_spread": {
            "cp": {
                "rows": len(cp_spreads),
                "p50": _fraction_number(_nearest_rank(cp_spreads, 0.50)),
                "p95": _fraction_number(_nearest_rank(cp_spreads, 0.95)),
            },
            "mate": {
                "rows": len(mate_spreads),
                "p50": _fraction_number(_nearest_rank(mate_spreads, 0.50)),
                "p95": _fraction_number(_nearest_rank(mate_spreads, 0.95)),
            },
        },
        "wdl_expectation_spread": {
            "rows": wdl_complete_rows,
            "p50": _fraction_number(_nearest_rank(wdl_spreads, 0.50)),
            "p95": _fraction_number(_nearest_rank(wdl_spreads, 0.95)),
        },
        "counts": {
            "split": _count_map(split_counts),
            "primary_stratum": _count_map(stratum_counts),
            "component_id": _count_map(component_counts),
            "source_sha256": _count_map(source_counts),
        },
        "probe_config_sha256_counts": _count_map(config_counts),
        "probe_configs": [
            {
                "sha256": digest,
                "count": config_counts[digest],
                "config": config_documents[digest],
            }
            for digest in sorted(config_documents)
        ],
        "kill_recommendation": {
            "decision": "KILL" if reasons else "GO",
            "kill": bool(reasons),
            "reasons": reasons,
            "target_met": {
                "bestmove": bestmove_flip_rows == 0,
                "kind": kind_flip_rows == 0,
                "sign": sign_flip_rows == 0,
            },
        },
    }
    return report


def _load_canonical_jsonl(
    path: Path,
) -> tuple[list[Mapping[str, object]], dict[str, object]]:
    resolved = path.resolve(strict=True)
    if resolved.is_symlink():
        raise StabilityContractError("input JSONL must not be a symlink")
    with resolved.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise StabilityContractError("input JSONL must be a regular file")
        payload = stream.read()
        after = os.fstat(stream.fileno())
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after or len(payload) != before.st_size:
        raise StabilityContractError("input JSONL changed while it was read")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise StabilityContractError("input JSONL must not contain a UTF-8 BOM")
    if b"\r" in payload:
        raise StabilityContractError("input JSONL must be LF-only")
    if payload and not payload.endswith(b"\n"):
        raise StabilityContractError("input JSONL is missing its final LF")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise StabilityContractError("input JSONL must be strict UTF-8") from error

    rows: list[Mapping[str, object]] = []
    for line_number, line in enumerate(text.splitlines(keepends=True), start=1):
        if line == "\n":
            raise StabilityContractError(
                f"input JSONL line {line_number} must not be blank"
            )
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise StabilityContractError(
                f"input JSONL line {line_number} is invalid JSON"
            ) from error
        if not isinstance(value, Mapping):
            raise StabilityContractError(
                f"input JSONL line {line_number} must be an object"
            )
        if common.canonical_json_line(value) != line:
            raise StabilityContractError(
                f"input JSONL line {line_number} is not canonical"
            )
        rows.append(value)
    provenance = {
        "path": str(resolved),
        "sha256": common.sha256_bytes(payload),
        "size_bytes": len(payload),
    }
    return rows, provenance


def analyze_file(input_path: Path, output_path: Path) -> dict[str, object]:
    """Analyze one canonical JSONL input and create a canonical sidecar."""

    if input_path.absolute() == output_path.absolute():
        raise StabilityContractError("input and output paths must differ")
    if output_path.exists():
        raise FileExistsError(
            f"refusing to overwrite stability sidecar: {output_path}"
        )
    rows, provenance = _load_canonical_jsonl(input_path)
    report = analyze_rows(rows, input_provenance=provenance)
    common.write_new_json(output_path, report)
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        report = analyze_file(args.input, args.output)
    except (OSError, common.MiningArtifactError, ValueError) as error:
        parser.exit(2, f"atomic label stability: {error}\n")
    output_sha256 = common.sha256_file(args.output)
    print(
        common.canonical_json_line(
            {
                "decision": _mapping(
                    report["kill_recommendation"], label="kill recommendation"
                )["decision"],
                "output": str(args.output.resolve()),
                "output_sha256": output_sha256,
                "schema": OUTPUT_SCHEMA,
            }
        ),
        end="",
    )
    recommendation = _mapping(
        report["kill_recommendation"], label="kill recommendation"
    )
    return 2 if recommendation["kill"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
