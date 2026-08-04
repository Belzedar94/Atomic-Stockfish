"""Validate and rank Atomic dual-probe disagreement records.

Ranking is lexicographic and deterministic:

1. opposite mate signs;
2. best-move disagreement from a stable teacher;
3. teacher regret on the current network's move;
4. WDL expectation gap;
5. centipawn gap.

Scores and WDL are interpreted from the side-to-move point of view.  Bounds
are forbidden: every preserved ``info`` record must contain an exact score.
Teacher stability deliberately means only identical best move, score kind and
score sign across repeats.  Numeric score spread is evidence, not part of the
stability predicate.
"""

from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
import json
from pathlib import Path
import re
from typing import Mapping, Sequence

from tools.atomic_mining import common, split_components


INPUT_SCHEMA = "atomic-dual-probe-v1"
OUTPUT_SCHEMA = "atomic-disagreement-rank-v1"
METRICS_SCHEMA = "atomic-disagreement-rank-metrics-v1"
UCI_MOVE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")

TOP_FIELDS = (
    "schema",
    "position_id",
    "component_id",
    "split",
    "candidate",
    "probe_config",
    "features",
    "current",
    "teacher_repeats",
    "teacher_on_current_move",
)
SNAPSHOT_FIELDS = (
    "bestmove",
    "ponder",
    "principal",
    "multipv",
    "raw_sha256",
    "raw_line_count",
)
INFO_FIELDS = (
    "depth",
    "seldepth",
    "nodes",
    "multipv",
    "score",
    "wdl",
    "pv",
)
SCORE_FIELDS = ("kind", "value", "bound")


class RankContractError(common.MiningArtifactError):
    """A dual-probe row cannot enter the disagreement ranking."""


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RankContractError(f"{label} must be an object")
    return value


def _list(value: object, *, label: str) -> list[object]:
    if not isinstance(value, list):
        raise RankContractError(f"{label} must be an array")
    return value


def _string(value: object, *, label: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise RankContractError(f"{label} must be a non-empty string")
    return value


def _integer(
    value: object,
    *,
    label: str,
    minimum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RankContractError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise RankContractError(f"{label} must be at least {minimum}")
    return value


def _boolean(value: object, *, label: str) -> bool:
    if not isinstance(value, bool):
        raise RankContractError(f"{label} must be boolean")
    return value


def _optional_move(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    move = _string(value, label=label)
    if UCI_MOVE.fullmatch(move) is None:
        raise RankContractError(f"{label} must be a UCI move or null")
    return move


def _sha(value: object, *, label: str) -> str:
    try:
        return common.require_lower_hex_sha256(value, label=label)
    except common.MiningArtifactError as error:
        raise RankContractError(str(error)) from error


def _exact_fields(
    value: Mapping[str, object],
    expected: Sequence[str],
    *,
    label: str,
) -> None:
    try:
        common.require_exact_fields(value, expected, label=label)
    except common.MiningArtifactError as error:
        raise RankContractError(str(error)) from error


def _validate_artifact(value: object, *, label: str) -> Mapping[str, object]:
    artifact = _mapping(value, label=label)
    _exact_fields(artifact, ("path", "sha256", "size_bytes"), label=label)
    _string(artifact["path"], label=f"{label}.path")
    _sha(artifact["sha256"], label=f"{label}.sha256")
    _integer(artifact["size_bytes"], label=f"{label}.size_bytes", minimum=0)
    return artifact


def _validate_candidate(
    value: object,
) -> split_components.PositionIdentity:
    candidate = _mapping(value, label="candidate")
    try:
        return split_components.validate_candidate(candidate)
    except split_components.SplitContractError as error:
        raise RankContractError(str(error)) from error


def _validate_probe_config(value: object) -> Mapping[str, object]:
    config = _mapping(value, label="probe_config")
    _exact_fields(
        config,
        (
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
        ),
        label="probe_config",
    )
    _validate_artifact(config["engine"], label="probe_config.engine")
    _validate_artifact(config["current_net"], label="probe_config.current_net")
    _validate_artifact(config["teacher_net"], label="probe_config.teacher_net")
    _integer(config["nodes"], label="probe_config.nodes", minimum=1)
    _integer(config["multipv"], label="probe_config.multipv", minimum=1)
    if _integer(config["threads"], label="probe_config.threads") != 1:
        raise RankContractError("probe_config.threads must equal 1")
    _integer(config["hash_mb"], label="probe_config.hash_mb", minimum=1)
    if _boolean(config["clear_hash"], label="probe_config.clear_hash") is not True:
        raise RankContractError("probe_config.clear_hash must be true")
    _integer(
        config["teacher_repeat_count"],
        label="probe_config.teacher_repeat_count",
        minimum=1,
    )
    if (
        config["probe_order_policy"]
        != "position-id-parity-alternating-v1"
    ):
        raise RankContractError(
            "probe_config.probe_order_policy must be "
            "position-id-parity-alternating-v1"
        )
    raw_options = _list(
        config["engine_options"], label="probe_config.engine_options"
    )
    expected_options: tuple[tuple[str, object], ...] = (
        ("UCI_Variant", "atomic"),
        ("Threads", 1),
        ("Hash", config["hash_mb"]),
        ("MultiPV", config["multipv"]),
        ("Ponder", False),
        ("SyzygyPath", "<empty>"),
        ("SyzygyProbeLimit", 0),
        ("Use NNUE", "pure"),
    )
    if len(raw_options) != len(expected_options):
        raise RankContractError(
            "probe_config.engine_options has the wrong option count"
        )
    for index, ((expected_name, expected_value), raw_option) in enumerate(
        zip(expected_options, raw_options)
    ):
        option = _mapping(
            raw_option, label=f"probe_config.engine_options[{index}]"
        )
        _exact_fields(
            option,
            ("name", "value"),
            label=f"probe_config.engine_options[{index}]",
        )
        if option["name"] != expected_name or option["value"] != expected_value:
            raise RankContractError(
                "probe_config.engine_options must equal the ordered, "
                "pure/T1/TB-off mining contract"
            )
    return config


def _validate_features(value: object) -> Mapping[str, object]:
    features = _mapping(value, label="features")
    _exact_fields(
        features,
        (
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
        ),
        label="features",
    )
    if features["classifier"] != "atomic-tactical-strata-v1":
        raise RankContractError(
            "features.classifier must be atomic-tactical-strata-v1"
        )
    for field in (
        "in_check",
        "post_capture",
        "post_capture_in_check",
        "teacher_move_capture",
        "teacher_move_gives_check",
        "teacher_move_promotion",
        "forced_chain",
    ):
        _boolean(features[field], label=f"features.{field}")
    primary = _string(features["primary_stratum"], label="features.primary_stratum")
    strata = _list(features["strata"], label="features.strata")
    normalized: list[str] = []
    for index, stratum in enumerate(strata):
        normalized.append(_string(stratum, label=f"features.strata[{index}]"))
    if len(set(normalized)) != len(normalized):
        raise RankContractError("features.strata must not contain duplicates")
    if primary not in normalized:
        raise RankContractError("features.primary_stratum must occur in strata")
    return features


def _validate_score(value: object, *, label: str) -> tuple[str, int]:
    score = _mapping(value, label=label)
    _exact_fields(score, SCORE_FIELDS, label=label)
    kind = score["kind"]
    if kind not in {"cp", "mate"}:
        raise RankContractError(f"{label}.kind must be cp or mate")
    score_value = _integer(score["value"], label=f"{label}.value")
    if score["bound"] != "exact":
        raise RankContractError(f"{label}.bound must be exact")
    return str(kind), score_value


def _validate_info(
    value: object,
    *,
    label: str,
) -> Mapping[str, object]:
    info = _mapping(value, label=label)
    _exact_fields(info, INFO_FIELDS, label=label)
    for field in ("depth", "seldepth", "nodes"):
        if info[field] is not None:
            _integer(info[field], label=f"{label}.{field}", minimum=0)
    if info["multipv"] is not None:
        _integer(info["multipv"], label=f"{label}.multipv", minimum=1)
    _validate_score(info["score"], label=f"{label}.score")
    if info["wdl"] is not None:
        wdl = _list(info["wdl"], label=f"{label}.wdl")
        if len(wdl) != 3:
            raise RankContractError(f"{label}.wdl must contain three integers")
        values = [
            _integer(item, label=f"{label}.wdl[{index}]", minimum=0)
            for index, item in enumerate(wdl)
        ]
        if sum(values) <= 0:
            raise RankContractError(f"{label}.wdl total must be positive")
    pv = _list(info["pv"], label=f"{label}.pv")
    for index, move in enumerate(pv):
        _optional_move(move, label=f"{label}.pv[{index}]")
    return info


def _validate_snapshot(
    value: object,
    *,
    label: str,
) -> Mapping[str, object]:
    snapshot = _mapping(value, label=label)
    _exact_fields(snapshot, SNAPSHOT_FIELDS, label=label)
    bestmove = _optional_move(snapshot["bestmove"], label=f"{label}.bestmove")
    _optional_move(snapshot["ponder"], label=f"{label}.ponder")
    principal = _validate_info(snapshot["principal"], label=f"{label}.principal")
    infos = _list(snapshot["multipv"], label=f"{label}.multipv")
    if not infos:
        raise RankContractError(f"{label}.multipv must not be empty")
    by_rank: dict[int, Mapping[str, object]] = {}
    for index, raw_info in enumerate(infos):
        info = _validate_info(raw_info, label=f"{label}.multipv[{index}]")
        rank_value = info["multipv"]
        rank = (
            1
            if rank_value is None
            else _integer(rank_value, label=f"{label}.multipv[{index}].multipv")
        )
        if rank in by_rank:
            raise RankContractError(f"{label}.multipv ranks must be unique")
        by_rank[rank] = info
    if sorted(by_rank) != list(range(1, len(by_rank) + 1)):
        raise RankContractError(f"{label}.multipv ranks must be contiguous from 1")
    if dict(principal) != dict(by_rank[1]):
        raise RankContractError(f"{label}.principal must equal MultiPV rank 1")
    principal_pv = _list(principal["pv"], label=f"{label}.principal.pv")
    if bestmove is None:
        if principal_pv:
            raise RankContractError(
                f"{label}.bestmove cannot be null when principal PV is non-empty"
            )
    elif not principal_pv or principal_pv[0] != bestmove:
        raise RankContractError(
            f"{label}.bestmove must equal the first principal PV move"
        )
    _sha(snapshot["raw_sha256"], label=f"{label}.raw_sha256")
    _integer(
        snapshot["raw_line_count"], label=f"{label}.raw_line_count", minimum=1
    )
    return snapshot


def validate_dual_probe_row(value: object) -> Mapping[str, object]:
    row = _mapping(value, label="row")
    _exact_fields(row, TOP_FIELDS, label="row")
    if row["schema"] != INPUT_SCHEMA:
        raise RankContractError(f"row.schema must be {INPUT_SCHEMA}")
    _sha(row["position_id"], label="position_id")
    _sha(row["component_id"], label="component_id")
    if row["split"] not in {"calibration", "confirmation", "technical-smoke"}:
        raise RankContractError(
            "split must be calibration, confirmation or technical-smoke"
        )
    identity = _validate_candidate(row["candidate"])
    if identity.candidate_schema == split_components.RESULT_BEARING_INPUT_SCHEMA:
        if row["position_id"] != identity.position_id:
            raise RankContractError(
                "result-bearing row.position_id must match the structural "
                "candidate identity"
            )
        if row["split"] == "technical-smoke":
            raise RankContractError(
                "result-bearing candidates cannot bypass the five-way split "
                "through technical-smoke"
            )
    config = _validate_probe_config(row["probe_config"])
    _validate_features(row["features"])
    current = _validate_snapshot(row["current"], label="current")
    repeats = _list(row["teacher_repeats"], label="teacher_repeats")
    expected_repeats = _integer(
        config["teacher_repeat_count"],
        label="probe_config.teacher_repeat_count",
    )
    if len(repeats) != expected_repeats:
        raise RankContractError(
            "teacher_repeats length must equal probe_config.teacher_repeat_count"
        )
    for index, snapshot in enumerate(repeats):
        _validate_snapshot(snapshot, label=f"teacher_repeats[{index}]")

    restricted = row["teacher_on_current_move"]
    current_bestmove = current["bestmove"]
    if current_bestmove is None:
        if restricted is not None:
            raise RankContractError(
                "teacher_on_current_move must be null when current.bestmove is null"
            )
    else:
        if restricted is None:
            raise RankContractError(
                "teacher_on_current_move is required for a current bestmove"
            )
        restricted_snapshot = _validate_snapshot(
            restricted, label="teacher_on_current_move"
        )
        if restricted_snapshot["bestmove"] != current_bestmove:
            raise RankContractError(
                "teacher_on_current_move.bestmove must equal current.bestmove"
            )
    return row


def _score(snapshot: Mapping[str, object]) -> tuple[str, int]:
    principal = _mapping(snapshot["principal"], label="snapshot.principal")
    score = _mapping(principal["score"], label="snapshot.principal.score")
    return str(score["kind"]), int(score["value"])


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


def _round_fraction(value: Fraction) -> int:
    if value >= 0:
        return (value.numerator * 2 + value.denominator) // (
            value.denominator * 2
        )
    positive = -value
    return -(
        (positive.numerator * 2 + positive.denominator)
        // (positive.denominator * 2)
    )


def _median_int(values: Sequence[int]) -> int:
    if not values:
        raise RankContractError("cannot take the median of an empty sequence")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return _round_fraction(Fraction(ordered[middle - 1] + ordered[middle], 2))


def _wdl_expectation_ppm(snapshot: Mapping[str, object]) -> int | None:
    principal = _mapping(snapshot["principal"], label="snapshot.principal")
    raw_wdl = principal["wdl"]
    if raw_wdl is None:
        return None
    wdl = _list(raw_wdl, label="snapshot.principal.wdl")
    wins, draws, losses = (int(wdl[0]), int(wdl[1]), int(wdl[2]))
    total = wins + draws + losses
    return _round_fraction(Fraction((wins - losses) * 1_000_000, total))


def _priority_class(vector: Sequence[int]) -> str:
    labels = (
        "opposite-mate",
        "stable-bestmove-disagreement",
        "teacher-regret",
        "wdl-gap",
        "cp-gap",
    )
    for label, value in zip(labels, vector):
        if value > 0:
            return label
    return "no-signal"


def rank_probe_row(value: object) -> dict[str, object]:
    row = validate_dual_probe_row(value)
    current = _mapping(row["current"], label="current")
    repeats = [
        _mapping(item, label=f"teacher_repeats[{index}]")
        for index, item in enumerate(
            _list(row["teacher_repeats"], label="teacher_repeats")
        )
    ]
    teacher_bestmoves = [snapshot["bestmove"] for snapshot in repeats]
    teacher_scores = [_score(snapshot) for snapshot in repeats]
    teacher_kinds = [kind for kind, _score_value in teacher_scores]
    teacher_values = [score_value for _kind, score_value in teacher_scores]
    teacher_signs = [_sign(score_value) for score_value in teacher_values]
    teacher_stable = (
        len(set(teacher_bestmoves)) == 1
        and len(set(teacher_kinds)) == 1
        and len(set(teacher_signs)) == 1
    )
    common_teacher_kind = (
        teacher_kinds[0] if len(set(teacher_kinds)) == 1 else None
    )
    common_teacher_sign = (
        teacher_signs[0] if len(set(teacher_signs)) == 1 else None
    )
    teacher_spread = (
        max(teacher_values) - min(teacher_values)
        if common_teacher_kind is not None
        else None
    )

    current_kind, current_value = _score(current)
    opposite_mate = bool(
        teacher_stable
        and common_teacher_kind == "mate"
        and current_kind == "mate"
        and common_teacher_sign is not None
        and common_teacher_sign * _sign(current_value) < 0
    )
    bestmove_disagreement = bool(
        teacher_stable
        and teacher_bestmoves[0] is not None
        and current["bestmove"] is not None
        and teacher_bestmoves[0] != current["bestmove"]
    )

    teacher_wdls = [_wdl_expectation_ppm(snapshot) for snapshot in repeats]
    teacher_wdl_median = (
        _median_int([int(item) for item in teacher_wdls])
        if all(item is not None for item in teacher_wdls)
        else None
    )
    current_wdl = _wdl_expectation_ppm(current)
    wdl_gap = (
        abs(teacher_wdl_median - current_wdl)
        if teacher_wdl_median is not None and current_wdl is not None
        else None
    )

    restricted_value = row["teacher_on_current_move"]
    restricted = (
        _mapping(restricted_value, label="teacher_on_current_move")
        if restricted_value is not None
        else None
    )
    restricted_wdl = (
        _wdl_expectation_ppm(restricted) if restricted is not None else None
    )
    teacher_regret = (
        max(0, teacher_wdl_median - restricted_wdl)
        if teacher_stable
        and teacher_wdl_median is not None
        and restricted_wdl is not None
        else None
    )
    restricted_score = _score(restricted) if restricted is not None else None
    teacher_regret_cp = (
        max(0, _median_int(teacher_values) - restricted_score[1])
        if teacher_stable
        and common_teacher_kind == "cp"
        and current_kind == "cp"
        and restricted_score is not None
        and restricted_score[0] == "cp"
        else None
    )
    teacher_regret_basis = (
        "wdl_ppm"
        if teacher_regret is not None
        else "cp"
        if teacher_regret_cp is not None
        else "none"
    )

    cp_gap = (
        abs(_median_int(teacher_values) - current_value)
        if common_teacher_kind == "cp" and current_kind == "cp"
        else None
    )
    vector = [
        int(opposite_mate),
        int(bestmove_disagreement),
        (
            teacher_regret
            if teacher_regret is not None
            else teacher_regret_cp
            if teacher_regret_cp is not None
            else 0
        ),
        wdl_gap or 0,
        cp_gap or 0,
    ]

    identity = _validate_candidate(row["candidate"])
    features = _mapping(row["features"], label="features")
    output = dict(row)
    output["schema"] = OUTPUT_SCHEMA
    output["rank"] = {
        "global_rank": 0,
        "priority_class": _priority_class(vector),
        "priority_vector": vector,
        "tie_breaker_position_id": row["position_id"],
        "teacher_stable": teacher_stable,
        "teacher_bestmove": (
            teacher_bestmoves[0] if teacher_stable else None
        ),
        "teacher_score_kind": common_teacher_kind,
        "teacher_score_sign": common_teacher_sign,
        "teacher_score_spread": teacher_spread,
        "opposite_mate": opposite_mate,
        "bestmove_disagreement": bestmove_disagreement,
        "teacher_regret_wdl_ppm": teacher_regret,
        "teacher_regret_cp": teacher_regret_cp,
        "teacher_regret_basis": teacher_regret_basis,
        "wdl_gap_ppm": wdl_gap,
        "cp_gap": cp_gap,
    }
    output["groups"] = {
        "component_id": row["component_id"],
        "split": row["split"],
        "primary_stratum": features["primary_stratum"],
        "source_sha256": identity.source_group_sha256,
        "source_game_id": identity.source_game_id,
    }
    return output


def _sort_key(row: Mapping[str, object]) -> tuple[object, ...]:
    rank = _mapping(row["rank"], label="rank")
    vector = _list(rank["priority_vector"], label="rank.priority_vector")
    return tuple(-int(value) for value in vector) + (str(row["position_id"]),)


def rank_disagreements(rows: Sequence[object]) -> list[dict[str, object]]:
    ranked = [rank_probe_row(row) for row in rows]
    position_ids = [str(row["position_id"]) for row in ranked]
    if len(set(position_ids)) != len(position_ids):
        raise RankContractError("position_id values must be unique")
    component_splits: dict[str, str] = {}
    for row in ranked:
        component_id = str(row["component_id"])
        split = str(row["split"])
        previous = component_splits.setdefault(component_id, split)
        if previous != split:
            raise RankContractError(
                "one component_id cannot occur in multiple splits"
            )
    ranked.sort(key=_sort_key)
    for index, row in enumerate(ranked, start=1):
        rank = dict(_mapping(row["rank"], label="rank"))
        rank["global_rank"] = index
        row["rank"] = rank
    return ranked


def _nearest_rank_percentile(values: Sequence[int], percentile: int) -> int:
    if not values:
        raise RankContractError("percentile requires at least one value")
    ordered = sorted(values)
    index = ((percentile * len(ordered) + 99) // 100) - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


def _count_mapping(values: Sequence[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def build_metrics(
    ranked: Sequence[Mapping[str, object]],
    *,
    input_path: Path,
    input_sha256: str,
    input_size_bytes: int,
    output_sha256: str,
) -> dict[str, object]:
    source_ids: list[str] = []
    game_ids: list[str] = []
    components: list[str] = []
    strata: list[str] = []
    splits: list[str] = []
    classes: list[str] = []
    spreads: dict[str, list[int]] = {"cp": [], "mate": []}
    stable = opposite = disagreements = regret_available = wdl_available = 0
    regret_bases: list[str] = []

    for row in ranked:
        groups = _mapping(row["groups"], label="groups")
        rank = _mapping(row["rank"], label="rank")
        source_ids.append(str(groups["source_sha256"]))
        game_ids.append(str(groups["source_game_id"]))
        components.append(str(groups["component_id"]))
        strata.append(str(groups["primary_stratum"]))
        splits.append(str(groups["split"]))
        classes.append(str(rank["priority_class"]))
        stable += int(bool(rank["teacher_stable"]))
        opposite += int(bool(rank["opposite_mate"]))
        disagreements += int(bool(rank["bestmove_disagreement"]))
        regret_basis = str(rank["teacher_regret_basis"])
        regret_bases.append(regret_basis)
        regret_available += int(regret_basis != "none")
        wdl_available += int(rank["wdl_gap_ppm"] is not None)
        kind = rank["teacher_score_kind"]
        spread = rank["teacher_score_spread"]
        if kind in spreads and spread is not None:
            spreads[str(kind)].append(int(spread))

    spread_metrics: dict[str, object] = {}
    for kind in ("cp", "mate"):
        values = spreads[kind]
        spread_metrics[kind] = (
            {
                "count": len(values),
                "min": min(values),
                "max": max(values),
                "p50": _nearest_rank_percentile(values, 50),
                "p95": _nearest_rank_percentile(values, 95),
            }
            if values
            else {"count": 0, "min": None, "max": None, "p50": None, "p95": None}
        )

    return {
        "schema": METRICS_SCHEMA,
        "input": {
            "path": str(input_path.resolve()),
            "sha256": input_sha256,
            "size_bytes": input_size_bytes,
            "row_count": len(ranked),
        },
        "ranked_output": {
            "schema": OUTPUT_SCHEMA,
            "sha256": output_sha256,
            "row_count": len(ranked),
        },
        "diversity": {
            "distinct_components": len(set(components)),
            "distinct_sources": len(set(source_ids)),
            "distinct_source_games": len(set(game_ids)),
            "counts_by_component": _count_mapping(components),
            "counts_by_source": _count_mapping(source_ids),
            "counts_by_source_game": _count_mapping(game_ids),
            "counts_by_primary_stratum": _count_mapping(strata),
            "counts_by_split": _count_mapping(splits),
        },
        "signals": {
            "teacher_stable_count": stable,
            "opposite_mate_count": opposite,
            "bestmove_disagreement_count": disagreements,
            "teacher_regret_available_count": regret_available,
            "counts_by_teacher_regret_basis": _count_mapping(regret_bases),
            "wdl_gap_available_count": wdl_available,
            "counts_by_priority_class": _count_mapping(classes),
        },
        "teacher_score_spread_by_kind": spread_metrics,
    }


def rank_file(
    input_path: Path,
    output_path: Path,
    metrics_output_path: Path,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    if output_path.absolute() == metrics_output_path.absolute():
        raise ValueError("output and metrics-output paths must differ")
    for path in (output_path, metrics_output_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite mining artifact: {path}")

    input_path = input_path.resolve(strict=True)
    input_sha256 = common.sha256_file(input_path)
    input_size_bytes = input_path.stat().st_size
    rows = common.load_jsonl(input_path)
    if not rows:
        raise RankContractError("input JSONL must contain at least one row")
    ranked = rank_disagreements(rows)
    output_payload = b"".join(common.canonical_json_bytes(row) for row in ranked)
    output_sha256 = common.sha256_bytes(output_payload)
    metrics = build_metrics(
        ranked,
        input_path=input_path,
        input_sha256=input_sha256,
        input_size_bytes=input_size_bytes,
        output_sha256=output_sha256,
    )

    common.write_new_json(metrics_output_path, metrics)
    try:
        common.write_new_bytes(output_path, output_payload)
    except BaseException:
        metrics_output_path.unlink()
        raise
    return ranked, metrics


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--metrics-output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    ranked, metrics = rank_file(args.input, args.output, args.metrics_output)
    print(
        json.dumps(
            {
                "input_sha256": metrics["input"]["sha256"],
                "output_sha256": metrics["ranked_output"]["sha256"],
                "positions_ranked": len(ranked),
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
