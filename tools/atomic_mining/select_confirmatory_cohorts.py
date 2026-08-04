"""Select matched diagnostic TOP and RANDOM cohorts from CONF rankings.

This selector is intentionally conservative.  It only accepts strict
``atomic-disagreement-rank-v1`` rows from the ``confirmation`` split, keeps
components whole, and matches TOP/RANDOM by the complete per-component
primary-stratum count vector.  That is stronger than aggregate matching and
provides a deterministic common-support reduction without ever relaxing a
matching key.

The output is diagnostic CONF evidence.  It is not REPLAY data, a training
result, or a strength result.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping, Sequence

from tools.atomic_mining import common
from tools.atomic_mining import rank_disagreements as ranker


RECEIPT_SCHEMA = "atomic-confirmatory-cohort-receipt-v1"
SCIENTIFIC_ROLE = "confirmation-diagnostic-only"
RANK_FIELDS = (
    "global_rank",
    "priority_class",
    "priority_vector",
    "tie_breaker_position_id",
    "teacher_stable",
    "teacher_bestmove",
    "teacher_score_kind",
    "teacher_score_sign",
    "teacher_score_spread",
    "opposite_mate",
    "bestmove_disagreement",
    "teacher_regret_wdl_ppm",
    "teacher_regret_cp",
    "teacher_regret_basis",
    "wdl_gap_ppm",
    "cp_gap",
)
GROUP_FIELDS = (
    "component_id",
    "split",
    "primary_stratum",
    "source_sha256",
    "source_game_id",
)


class CohortSelectionError(common.MiningArtifactError):
    """The confirmatory cohort contract cannot be satisfied."""


@dataclass(frozen=True)
class SelectionConfig:
    target_rows: int
    seed: str
    max_rows_per_source_game: int = 2
    max_rows_per_component: int = 2
    dominance_ceiling_ppm: int = 100_000
    min_rows: int = 1


@dataclass(frozen=True)
class _Component:
    component_id: str
    rows: tuple[dict[str, object], ...]
    signature: tuple[tuple[str, int], ...]
    best_vector: tuple[int, int, int, int, int]
    best_rank: int
    source_game_counts: tuple[tuple[str, int], ...]

    @property
    def size(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class _Pair:
    top: _Component
    random: _Component


@dataclass(frozen=True)
class SelectionResult:
    top_rows: tuple[dict[str, object], ...]
    random_rows: tuple[dict[str, object], ...]
    top_component_ids: tuple[str, ...]
    random_component_ids: tuple[str, ...]
    counts_by_stratum: Mapping[str, int]
    reasons: Mapping[str, int]
    diversity: Mapping[str, object]
    dominance: Mapping[str, object]


def _integer(
    value: object,
    *,
    label: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CohortSelectionError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise CohortSelectionError(f"{label} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise CohortSelectionError(f"{label} must be at most {maximum}")
    return value


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CohortSelectionError(f"{label} must be an object")
    return value


def _list(value: object, *, label: str) -> list[object]:
    if not isinstance(value, list):
        raise CohortSelectionError(f"{label} must be an array")
    return value


def _exact_fields(
    value: Mapping[str, object],
    expected: Sequence[str],
    *,
    label: str,
) -> None:
    try:
        common.require_exact_fields(value, expected, label=label)
    except common.MiningArtifactError as error:
        raise CohortSelectionError(str(error)) from error


def _validate_config(config: SelectionConfig) -> None:
    _integer(config.target_rows, label="target_rows", minimum=1)
    if not isinstance(config.seed, str) or not config.seed:
        raise CohortSelectionError("seed must be a non-empty string")
    _integer(
        config.max_rows_per_source_game,
        label="max_rows_per_source_game",
        minimum=1,
    )
    _integer(
        config.max_rows_per_component,
        label="max_rows_per_component",
        minimum=1,
    )
    _integer(
        config.dominance_ceiling_ppm,
        label="dominance_ceiling_ppm",
        minimum=1,
        maximum=1_000_000,
    )
    _integer(config.min_rows, label="min_rows", minimum=1)
    if config.min_rows > config.target_rows:
        raise CohortSelectionError("min_rows cannot exceed target_rows")


def _validate_ranked_row(value: object) -> dict[str, object]:
    row = _mapping(value, label="row")
    _exact_fields(
        row,
        (*ranker.TOP_FIELDS, "rank", "groups"),
        label="row",
    )
    if row["schema"] != ranker.OUTPUT_SCHEMA:
        raise CohortSelectionError(
            f"row.schema must be {ranker.OUTPUT_SCHEMA}"
        )
    if row["split"] != "confirmation":
        raise CohortSelectionError("selector accepts only split=confirmation")

    base = {field: row[field] for field in ranker.TOP_FIELDS}
    base["schema"] = ranker.INPUT_SCHEMA
    try:
        expected = ranker.rank_probe_row(base)
    except common.MiningArtifactError as error:
        raise CohortSelectionError(str(error)) from error

    supplied_rank = _mapping(row["rank"], label="row.rank")
    _exact_fields(supplied_rank, RANK_FIELDS, label="row.rank")
    global_rank = _integer(
        supplied_rank["global_rank"], label="row.rank.global_rank", minimum=1
    )
    expected_rank = dict(_mapping(expected["rank"], label="expected.rank"))
    expected_rank["global_rank"] = global_rank
    if dict(supplied_rank) != expected_rank:
        raise CohortSelectionError(
            "row.rank does not match the deterministic rank metrics"
        )

    supplied_groups = _mapping(row["groups"], label="row.groups")
    _exact_fields(supplied_groups, GROUP_FIELDS, label="row.groups")
    if dict(supplied_groups) != dict(
        _mapping(expected["groups"], label="expected.groups")
    ):
        raise CohortSelectionError(
            "row.groups does not match candidate provenance and split"
        )
    return dict(row)


def _authenticate_complete_ranking(
    rows: Sequence[object],
) -> list[dict[str, object]]:
    if not rows:
        raise CohortSelectionError("ranked input must contain at least one row")
    supplied: list[dict[str, object]] = []
    bases: list[dict[str, object]] = []
    for index, value in enumerate(rows):
        row = _mapping(value, label=f"row[{index}]")
        _exact_fields(
            row,
            (*ranker.TOP_FIELDS, "rank", "groups"),
            label=f"row[{index}]",
        )
        if row["schema"] != ranker.OUTPUT_SCHEMA:
            raise CohortSelectionError(
                f"row[{index}].schema must be {ranker.OUTPUT_SCHEMA}"
            )
        base = {field: row[field] for field in ranker.TOP_FIELDS}
        base["schema"] = ranker.INPUT_SCHEMA
        supplied.append(dict(row))
        bases.append(base)
    try:
        expected = ranker.rank_disagreements(bases)
    except common.MiningArtifactError as error:
        raise CohortSelectionError(str(error)) from error
    if supplied != expected:
        raise CohortSelectionError(
            "ranked input must equal the complete deterministic ranking in "
            "canonical order, including global_rank"
        )
    return expected


def _component_priority_key(component: _Component) -> tuple[object, ...]:
    return tuple(-value for value in component.best_vector) + (
        component.best_rank,
        component.component_id,
    )


def _row_key(row: Mapping[str, object]) -> tuple[int, str]:
    rank = _mapping(row["rank"], label="row.rank")
    return int(rank["global_rank"]), str(row["position_id"])


def _build_components(
    rows: Sequence[object],
) -> list[_Component]:
    authenticated = _authenticate_complete_ranking(rows)
    validated = [_validate_ranked_row(row) for row in authenticated]

    global_ranks = [
        int(_mapping(row["rank"], label="row.rank")["global_rank"])
        for row in validated
    ]
    if len(set(global_ranks)) != len(global_ranks):
        raise CohortSelectionError("global_rank values must be unique")
    if sorted(global_ranks) != list(range(1, len(global_ranks) + 1)):
        raise CohortSelectionError(
            "global_rank values must be contiguous from 1"
        )

    by_component: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in validated:
        by_component[str(row["component_id"])].append(row)

    components: list[_Component] = []
    for component_id, component_rows in by_component.items():
        component_rows.sort(key=_row_key)
        strata = Counter(
            str(_mapping(row["groups"], label="row.groups")["primary_stratum"])
            for row in component_rows
        )
        vectors = [
            tuple(
                int(item)
                for item in _list(
                    _mapping(row["rank"], label="row.rank")["priority_vector"],
                    label="row.rank.priority_vector",
                )
            )
            for row in component_rows
        ]
        if any(len(vector) != 5 for vector in vectors):
            raise CohortSelectionError(
                "rank.priority_vector must contain exactly five integers"
            )
        best_vector = max(vectors)
        best_rank = min(
            int(_mapping(row["rank"], label="row.rank")["global_rank"])
            for row in component_rows
            if tuple(
                int(item)
                for item in _list(
                    _mapping(row["rank"], label="row.rank")["priority_vector"],
                    label="row.rank.priority_vector",
                )
            )
            == best_vector
        )
        source_games = Counter(
            str(_mapping(row["groups"], label="row.groups")["source_game_id"])
            for row in component_rows
        )
        components.append(
            _Component(
                component_id=component_id,
                rows=tuple(component_rows),
                signature=tuple(sorted(strata.items())),
                best_vector=best_vector,  # type: ignore[arg-type]
                best_rank=best_rank,
                source_game_counts=tuple(sorted(source_games.items())),
            )
        )
    components.sort(key=_component_priority_key)
    return components


def _seed_key(
    component: _Component,
    *,
    seed: str,
    structural_input_sha256: str,
) -> tuple[str, str]:
    return (
        common.derive_id(
            "atomic-confirmatory-random-order-v1",
            seed,
            structural_input_sha256,
            component.component_id,
        ),
        component.component_id,
    )


def _structural_input_sha256(
    components: Sequence[_Component],
) -> str:
    """Bind randomization only to result-free, validated downstream fields."""

    rows = sorted(
        (
            {
                "position_id": row["position_id"],
                "component_id": row["component_id"],
                "split": row["split"],
                "rank": row["rank"],
                "groups": row["groups"],
            }
            for component in components
            for row in component.rows
        ),
        key=lambda row: str(row["position_id"]),
    )
    return common.sha256_bytes(
        common.canonical_json_bytes(
            {
                "schema": "atomic-confirmatory-structural-input-v1",
                "rows": rows,
            }
        )
    )


def _fits_source_game_cap(
    counts: Counter[str],
    component: _Component,
    *,
    cap: int,
) -> bool:
    return all(
        counts[source_game] + increment <= cap
        for source_game, increment in component.source_game_counts
    )


def _add_source_games(
    counts: Counter[str],
    component: _Component,
) -> None:
    counts.update(dict(component.source_game_counts))


def _row_counts_by_source_game(components: Sequence[_Component]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for component in components:
        _add_source_games(counts, component)
    return counts


def _violations(
    pairs: Sequence[_Pair],
    *,
    ceiling_ppm: int,
) -> list[tuple[str, str, int]]:
    if not pairs:
        return []
    total = sum(pair.top.size for pair in pairs)
    violations: list[tuple[str, str, int]] = []
    for side in ("top", "random"):
        components = [
            pair.top if side == "top" else pair.random for pair in pairs
        ]
        for component in components:
            if component.size * 1_000_000 > ceiling_ppm * total:
                violations.append(
                    (side, f"component:{component.component_id}", component.size)
                )
        for source_game, count in _row_counts_by_source_game(components).items():
            if count * 1_000_000 > ceiling_ppm * total:
                violations.append((side, f"source_game:{source_game}", count))
    violations.sort(key=lambda item: (-item[2], item[0], item[1]))
    return violations


def _pair_contributes(pair: _Pair, side: str, group: str) -> bool:
    component = pair.top if side == "top" else pair.random
    kind, value = group.split(":", 1)
    if kind == "component":
        return component.component_id == value
    return any(source_game == value for source_game, _ in component.source_game_counts)


def _apply_dominance_ceiling(
    pairs: list[_Pair],
    *,
    ceiling_ppm: int,
    reasons: Counter[str],
) -> list[_Pair]:
    retained = list(pairs)
    while True:
        violations = _violations(retained, ceiling_ppm=ceiling_ppm)
        if not violations:
            return retained
        side, group, _count = violations[0]
        candidates = [
            index
            for index, pair in enumerate(retained)
            if _pair_contributes(pair, side, group)
        ]
        if not candidates:
            raise AssertionError("dominance violation has no contributing pair")
        # Drop the lowest-priority TOP pair that removes the offending group.
        remove_index = max(candidates)
        retained.pop(remove_index)
        reasons["pairs_removed_by_dominance_ceiling"] += 1


def _flatten(pairs: Sequence[_Pair], *, side: str) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for pair in pairs:
        component = pair.top if side == "top" else pair.random
        rows.extend(component.rows)
    return tuple(rows)


def _counts_by_stratum(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, int]:
    counts = Counter(
        str(_mapping(row["groups"], label="row.groups")["primary_stratum"])
        for row in rows
    )
    return dict(sorted(counts.items()))


def _max_share_ppm(counts: Mapping[str, int], total: int) -> int:
    if not counts or total <= 0:
        return 0
    return max((count * 1_000_000 + total - 1) // total for count in counts.values())


def _diversity(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    sources = Counter(
        str(_mapping(row["groups"], label="row.groups")["source_sha256"])
        for row in rows
    )
    games = Counter(
        str(_mapping(row["groups"], label="row.groups")["source_game_id"])
        for row in rows
    )
    components = Counter(str(row["component_id"]) for row in rows)
    return {
        "distinct_sources": len(sources),
        "distinct_source_games": len(games),
        "distinct_components": len(components),
        "counts_by_source": dict(sorted(sources.items())),
        "counts_by_source_game": dict(sorted(games.items())),
        "counts_by_component": dict(sorted(components.items())),
    }


def _dominance(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    total = len(rows)
    games = Counter(
        str(_mapping(row["groups"], label="row.groups")["source_game_id"])
        for row in rows
    )
    components = Counter(str(row["component_id"]) for row in rows)
    return {
        "max_source_game_share_ppm": _max_share_ppm(games, total),
        "max_component_share_ppm": _max_share_ppm(components, total),
    }


def select_confirmatory_cohorts(
    rows: Sequence[object],
    *,
    input_sha256: str,
    config: SelectionConfig,
) -> SelectionResult:
    _validate_config(config)
    try:
        common.require_lower_hex_sha256(input_sha256, label="input_sha256")
    except common.MiningArtifactError as error:
        raise CohortSelectionError(str(error)) from error

    components = _build_components(rows)
    structural_input_sha256 = _structural_input_sha256(components)
    reasons: Counter[str] = Counter()
    eligible: list[_Component] = []
    for component in components:
        if component.size > config.max_rows_per_component:
            reasons["components_excluded_over_component_cap"] += 1
            continue
        eligible.append(component)

    by_signature: dict[
        tuple[tuple[str, int], ...], list[_Component]
    ] = defaultdict(list)
    for component in eligible:
        by_signature[component.signature].append(component)

    top_pool: list[_Component] = []
    random_pool: dict[
        tuple[tuple[str, int], ...], list[_Component]
    ] = {}
    for signature, members in by_signature.items():
        ordered = sorted(members, key=_component_priority_key)
        capacity = len(ordered) // 2
        if capacity == 0:
            reasons["components_excluded_no_common_support"] += len(ordered)
            continue
        top_pool.extend(ordered[:capacity])
        random_pool[signature] = sorted(
            ordered,
            key=lambda component: _seed_key(
                component,
                seed=config.seed,
                structural_input_sha256=structural_input_sha256,
            ),
        )

    top_pool.sort(key=_component_priority_key)
    top_source_games: Counter[str] = Counter()
    provisional_top: list[_Component] = []
    selected_rows = 0
    for top in top_pool:
        if selected_rows + top.size > config.target_rows:
            reasons["top_components_skipped_target_max"] += 1
            continue
        if not _fits_source_game_cap(
            top_source_games,
            top,
            cap=config.max_rows_per_source_game,
        ):
            reasons["top_components_skipped_source_game_cap"] += 1
            continue
        provisional_top.append(top)
        selected_rows += top.size
        _add_source_games(top_source_games, top)
        if selected_rows == config.target_rows:
            break

    selected_top_ids = {component.component_id for component in provisional_top}
    used_random: set[str] = set()
    random_source_games: Counter[str] = Counter()
    pairs: list[_Pair] = []
    for top in provisional_top:
        selected_random: _Component | None = None
        for random_component in random_pool[top.signature]:
            if (
                random_component.component_id in selected_top_ids
                or random_component.component_id in used_random
            ):
                continue
            if not _fits_source_game_cap(
                random_source_games,
                random_component,
                cap=config.max_rows_per_source_game,
            ):
                reasons["random_candidates_skipped_source_game_cap"] += 1
                continue
            selected_random = random_component
            break
        if selected_random is None:
            reasons["top_components_dropped_no_random_match"] += 1
            continue
        pairs.append(_Pair(top=top, random=selected_random))
        used_random.add(selected_random.component_id)
        _add_source_games(random_source_games, selected_random)

    pairs = _apply_dominance_ceiling(
        pairs,
        ceiling_ppm=config.dominance_ceiling_ppm,
        reasons=reasons,
    )
    top_rows = _flatten(pairs, side="top")
    random_rows = _flatten(pairs, side="random")
    if not top_rows:
        raise CohortSelectionError(
            "common support and dominance gates selected zero rows"
        )
    if len(top_rows) < config.min_rows:
        raise CohortSelectionError(
            f"selected rows {len(top_rows)} are below min_rows {config.min_rows}"
        )
    if len(top_rows) != len(random_rows):
        raise AssertionError("TOP and RANDOM row totals differ")
    top_strata = _counts_by_stratum(top_rows)
    random_strata = _counts_by_stratum(random_rows)
    if top_strata != random_strata:
        raise AssertionError("TOP and RANDOM strata counts differ")
    top_components = tuple(pair.top.component_id for pair in pairs)
    random_components = tuple(pair.random.component_id for pair in pairs)
    if set(top_components) & set(random_components):
        raise AssertionError("TOP and RANDOM components overlap")

    top_dominance = _dominance(top_rows)
    random_dominance = _dominance(random_rows)
    for side, evidence in (
        ("top", top_dominance),
        ("random", random_dominance),
    ):
        if int(evidence["max_source_game_share_ppm"]) > config.dominance_ceiling_ppm:
            raise CohortSelectionError(f"{side} source game exceeds dominance ceiling")
        if int(evidence["max_component_share_ppm"]) > config.dominance_ceiling_ppm:
            raise CohortSelectionError(f"{side} component exceeds dominance ceiling")

    reasons["selected_pairs"] = len(pairs)
    reasons["selected_rows_per_cohort"] = len(top_rows)
    reasons["requested_rows_not_selected"] = config.target_rows - len(top_rows)
    return SelectionResult(
        top_rows=top_rows,
        random_rows=random_rows,
        top_component_ids=top_components,
        random_component_ids=random_components,
        counts_by_stratum=top_strata,
        reasons=dict(sorted(reasons.items())),
        diversity={
            "top": _diversity(top_rows),
            "random": _diversity(random_rows),
        },
        dominance={
            "ceiling_ppm": config.dominance_ceiling_ppm,
            "top": top_dominance,
            "random": random_dominance,
        },
    )


def _artifact_record(path: Path, payload: bytes, rows: int) -> dict[str, object]:
    return {
        "path": str(path.absolute()),
        "sha256": common.sha256_bytes(payload),
        "size_bytes": len(payload),
        "row_count": rows,
    }


def _component_ids_sha256(ids: Sequence[str], *, cohort: str) -> str:
    return common.sha256_bytes(
        common.canonical_json_bytes(
            {
                "schema": "atomic-confirmatory-component-inventory-v1",
                "cohort": cohort,
                "component_ids": list(ids),
            }
        )
    )


def _authenticate_rank_metrics(
    metrics_path: Path,
    expected_metrics_sha256: str,
    *,
    ranked_rows: Sequence[Mapping[str, object]],
    ranked_path: Path,
    ranked_sha256: str,
    ranked_size_bytes: int,
    ranked_row_count: int,
) -> dict[str, object]:
    try:
        expected_sha256 = common.require_lower_hex_sha256(
            expected_metrics_sha256,
            label="expected rank metrics SHA-256",
        )
    except common.MiningArtifactError as error:
        raise CohortSelectionError(str(error)) from error
    requested = Path(metrics_path).absolute()
    if requested.is_symlink():
        raise CohortSelectionError("rank metrics must not be a symlink")
    try:
        payload = common.read_stable_file_bytes(
            requested, label="rank metrics"
        )
    except common.MiningArtifactError as error:
        raise CohortSelectionError(str(error)) from error
    observed_sha256 = common.sha256_bytes(payload)
    if observed_sha256 != expected_sha256:
        raise CohortSelectionError(
            "rank metrics differ from the caller trust anchor"
        )
    try:
        decoded = payload.decode("utf-8", errors="strict")
        value = json.loads(decoded)
        canonical = common.canonical_json_bytes(value)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise CohortSelectionError(
            "rank metrics must be one canonical UTF-8 JSON object"
        ) from error
    if canonical != payload or not isinstance(value, dict):
        raise CohortSelectionError(
            "rank metrics must be one canonical UTF-8 JSON object"
        )
    metrics = value
    _exact_fields(
        metrics,
        (
            "schema",
            "input",
            "ranked_output",
            "diversity",
            "signals",
            "teacher_score_spread_by_kind",
        ),
        label="rank metrics",
    )
    if metrics["schema"] != ranker.METRICS_SCHEMA:
        raise CohortSelectionError(
            f"rank metrics.schema must be {ranker.METRICS_SCHEMA}"
        )
    metrics_input = _mapping(
        metrics["input"], label="rank metrics.input"
    )
    _exact_fields(
        metrics_input,
        ("path", "sha256", "size_bytes", "row_count"),
        label="rank metrics.input",
    )
    if not isinstance(metrics_input["path"], str) or not metrics_input["path"]:
        raise CohortSelectionError(
            "rank metrics.input.path must be a non-empty string"
        )
    try:
        metrics_input_sha256 = common.require_lower_hex_sha256(
            metrics_input["sha256"],
            label="rank metrics.input.sha256",
        )
    except common.MiningArtifactError as error:
        raise CohortSelectionError(str(error)) from error
    metrics_input_size = _integer(
        metrics_input["size_bytes"],
        label="rank metrics.input.size_bytes",
        minimum=0,
    )
    metrics_input_rows = _integer(
        metrics_input["row_count"],
        label="rank metrics.input.row_count",
        minimum=1,
    )
    if metrics_input_rows != ranked_row_count:
        raise CohortSelectionError(
            "rank metrics input row count differs from ranked input"
        )
    ranked_output = _mapping(
        metrics["ranked_output"], label="rank metrics.ranked_output"
    )
    _exact_fields(
        ranked_output,
        ("schema", "sha256", "row_count"),
        label="rank metrics.ranked_output",
    )
    if ranked_output["schema"] != ranker.OUTPUT_SCHEMA:
        raise CohortSelectionError(
            "rank metrics ranked_output.schema differs"
        )
    if ranked_output["sha256"] != ranked_sha256:
        raise CohortSelectionError(
            "rank metrics do not bind the ranked input SHA-256"
        )
    if ranked_output["row_count"] != ranked_row_count:
        raise CohortSelectionError(
            "rank metrics do not bind the ranked input row count"
        )
    expected_metrics = ranker.build_metrics(
        ranked_rows,
        input_path=Path(str(metrics_input["path"])),
        input_sha256=metrics_input_sha256,
        input_size_bytes=metrics_input_size,
        output_sha256=ranked_sha256,
    )
    if metrics != expected_metrics:
        raise CohortSelectionError(
            "rank metrics do not equal the deterministic metrics for the "
            "authenticated ranked input"
        )
    return {
        "path": str(requested),
        "sha256": observed_sha256,
        "size_bytes": len(payload),
        "ranked_path": str(ranked_path),
        "ranked_sha256": ranked_sha256,
        "ranked_size_bytes": ranked_size_bytes,
        "ranked_row_count": ranked_row_count,
    }


def write_selection(
    *,
    input_path: Path,
    rank_metrics_path: Path,
    expected_rank_metrics_sha256: str,
    top_path: Path,
    random_path: Path,
    receipt_path: Path,
    config: SelectionConfig,
) -> tuple[SelectionResult, dict[str, object]]:
    destinations = (top_path, random_path, receipt_path)
    absolute_destinations = [path.absolute() for path in destinations]
    if len(set(absolute_destinations)) != len(absolute_destinations):
        raise ValueError("top, random and receipt paths must differ")
    for path in destinations:
        if path.exists():
            raise FileExistsError(f"refusing to overwrite mining artifact: {path}")

    input_snapshot = common.load_jsonl_snapshot(input_path)
    input_path = input_snapshot.path
    input_sha256 = input_snapshot.sha256
    input_size = input_snapshot.size_bytes
    rows = list(input_snapshot.rows)
    authenticated_rows = _authenticate_complete_ranking(rows)
    rank_metrics = _authenticate_rank_metrics(
        rank_metrics_path,
        expected_rank_metrics_sha256,
        ranked_rows=authenticated_rows,
        ranked_path=input_path,
        ranked_sha256=input_sha256,
        ranked_size_bytes=input_size,
        ranked_row_count=len(rows),
    )
    result = select_confirmatory_cohorts(
        authenticated_rows,
        input_sha256=input_sha256,
        config=config,
    )
    structural_input_sha256 = _structural_input_sha256(
        _build_components(authenticated_rows)
    )
    top_payload = b"".join(
        common.canonical_json_bytes(row) for row in result.top_rows
    )
    random_payload = b"".join(
        common.canonical_json_bytes(row) for row in result.random_rows
    )
    top_artifact = _artifact_record(top_path, top_payload, len(result.top_rows))
    random_artifact = _artifact_record(
        random_path, random_payload, len(result.random_rows)
    )
    receipt: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        "scientific_role": SCIENTIFIC_ROLE,
        "input": {
            "path": str(input_path),
            "sha256": input_sha256,
            "size_bytes": input_size,
            "row_count": len(rows),
            "required_schema": ranker.OUTPUT_SCHEMA,
            "required_split": "confirmation",
            "ranking_metrics": rank_metrics,
        },
        "config": {
            "target_rows": config.target_rows,
            "seed": config.seed,
            "structural_input_sha256": structural_input_sha256,
            "seed_sha256": common.derive_id(
                "atomic-confirmatory-selection-seed-v1",
                config.seed,
                structural_input_sha256,
            ),
            "max_rows_per_source_game": config.max_rows_per_source_game,
            "max_rows_per_component": config.max_rows_per_component,
            "dominance_ceiling_ppm": config.dominance_ceiling_ppm,
            "min_rows": config.min_rows,
        },
        "artifacts": {
            "top": top_artifact,
            "random": random_artifact,
        },
        "selection": {
            "requested_rows": config.target_rows,
            "selected_rows_per_cohort": len(result.top_rows),
            "selected_components": {
                "top": len(result.top_component_ids),
                "random": len(result.random_component_ids),
            },
            "counts_by_stratum": dict(result.counts_by_stratum),
            "common_support_reduced": len(result.top_rows) < config.target_rows,
            "reasons": dict(result.reasons),
            "diversity": dict(result.diversity),
            "dominance": dict(result.dominance),
        },
        "proof": {
            "zero_component_overlap": not (
                set(result.top_component_ids) & set(result.random_component_ids)
            ),
            "exact_row_count_match": len(result.top_rows)
            == len(result.random_rows),
            "exact_strata_match": _counts_by_stratum(result.top_rows)
            == _counts_by_stratum(result.random_rows),
            "components_are_complete": True,
            "top_component_ids_sha256": _component_ids_sha256(
                result.top_component_ids, cohort="top"
            ),
            "random_component_ids_sha256": _component_ids_sha256(
                result.random_component_ids, cohort="random"
            ),
        },
    }
    receipt_payload = common.canonical_json_bytes(receipt)

    try:
        for path, payload in (
            (top_path, top_payload),
            (random_path, random_payload),
        ):
            common.write_new_bytes(path, payload)
    except BaseException:
        for path in (random_path, top_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise
    common.write_new_bytes(receipt_path, receipt_payload)
    return result, receipt


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--rank-metrics", required=True, type=Path)
    parser.add_argument("--rank-metrics-sha256", required=True)
    parser.add_argument("--top-output", required=True, type=Path)
    parser.add_argument("--random-output", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--target-rows", required=True, type=int)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--max-rows-per-source-game", type=int, default=2)
    parser.add_argument("--max-rows-per-component", type=int, default=2)
    parser.add_argument("--dominance-ceiling-ppm", type=int, default=100_000)
    parser.add_argument("--min-rows", type=int, default=1)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = SelectionConfig(
        target_rows=args.target_rows,
        seed=args.seed,
        max_rows_per_source_game=args.max_rows_per_source_game,
        max_rows_per_component=args.max_rows_per_component,
        dominance_ceiling_ppm=args.dominance_ceiling_ppm,
        min_rows=args.min_rows,
    )
    result, receipt = write_selection(
        input_path=args.input,
        rank_metrics_path=args.rank_metrics,
        expected_rank_metrics_sha256=args.rank_metrics_sha256,
        top_path=args.top_output,
        random_path=args.random_output,
        receipt_path=args.receipt,
        config=config,
    )
    print(
        json.dumps(
            {
                "input_sha256": receipt["input"]["sha256"],
                "selected_rows_per_cohort": len(result.top_rows),
                "top_sha256": receipt["artifacts"]["top"]["sha256"],
                "random_sha256": receipt["artifacts"]["random"]["sha256"],
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
