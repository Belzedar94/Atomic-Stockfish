from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jsonschema.validators import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_FILE = ROOT / "schemas" / "atomic-nnue-v3.json"
STATS_SCHEMA_FILE = ROOT / "schemas" / "atomic-v3-dataset-stats-v1.json"
LEDGER_SCHEMA_FILE = ROOT / "schemas" / "atomic-trajectory-ledger-v1.json"
INDEX_COVERAGE_SCHEMA_FILE = ROOT / "schemas" / "atomic-v3-index-coverage-v1.json"
COVERAGE_POLICY_SCHEMA_FILE = ROOT / "schemas" / "atomic-v3-coverage-policy-v1.json"
SPLIT_AUDIT_SCHEMA_FILE = ROOT / "schemas" / "atomic-v3-split-audit-v1.json"
TRAINING_RUN_SCHEMA_FILE = (
    ROOT / "schemas" / "atomic-v3-training-run-manifest-v1.json"
)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AssertionError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_lf_json(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    assert b"\r" not in payload, f"{path.name} must use LF line endings"
    assert payload.endswith(b"\n") and not payload.endswith(b"\n\n")
    return json.loads(
        payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
    )


def _load_contract() -> dict[str, Any]:
    return _load_lf_json(CONTRACT_FILE)


def test_json_instance_schemas_are_valid_draft_2020_12() -> None:
    for path in (
        STATS_SCHEMA_FILE,
        COVERAGE_POLICY_SCHEMA_FILE,
        SPLIT_AUDIT_SCHEMA_FILE,
        TRAINING_RUN_SCHEMA_FILE,
    ):
        schema = _load_lf_json(path)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(schema)
        assert schema["x-wire"] == {
            "encoding": "utf-8",
            "bom": "forbidden",
            "canonical_json": {
                "key_order": "schema-declaration-order",
                "separators": [",", ":"],
                "ensure_ascii": False,
                "allow_nan": False,
                "insignificant_whitespace": "forbidden",
            },
            "trailing_lf_count": 1,
            "maximum_bytes": 16 * 1024 * 1024,
            "file_type": "regular",
            "symbolic_links": "forbidden",
            "snapshot_stability": "same-handle-fstat-before-and-after",
        }


def _assert_contiguous_fields(fields: list[dict[str, Any]], expected_size: int) -> None:
    next_offset = 0
    for field in fields:
        assert field["offset"] == next_offset, field["name"]
        next_offset += field["size"]
    assert next_offset == expected_size


def _u32(value: object) -> int:
    assert isinstance(value, str)
    assert re.fullmatch(r"0x[0-9A-F]{8}", value)
    return int(value, 16)


def _slice(contract: dict[str, Any], slice_id: str) -> dict[str, Any]:
    matches = [item for item in contract["feature_slices"] if item["id"] == slice_id]
    assert len(matches) == 1
    return matches[0]


def _leaper_edges(deltas: tuple[tuple[int, int], ...]) -> int:
    count = 0
    for rank in range(8):
        for file in range(8):
            for df, dr in deltas:
                if 0 <= file + df < 8 and 0 <= rank + dr < 8:
                    count += 1
    return count


def _slider_edges(directions: tuple[tuple[int, int], ...]) -> int:
    count = 0
    for rank in range(8):
        for file in range(8):
            for df, dr in directions:
                target_file = file + df
                target_rank = rank + dr
                while 0 <= target_file < 8 and 0 <= target_rank < 8:
                    count += 1
                    target_file += df
                    target_rank += dr
    return count


def _fnv1a32(payload: bytes) -> int:
    value = 0x811C9DC5
    for byte in payload:
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return value


def _fold_hashes(values: list[int]) -> int:
    result = 0
    for value in values:
        result = ((result << 1) | (result >> 31)) & 0xFFFFFFFF
        result ^= value
    return result


def _orient_square(perspective: str, king_square: int, square: int) -> int:
    vertical = 56 if perspective == "BLACK" else 0
    oriented_king = king_square ^ vertical
    horizontal = 7 if oriented_king % 8 < 4 else 0
    return square ^ vertical ^ horizontal


def _king_bucket(oriented_king: int) -> int:
    file_index = oriented_king % 8
    rank_index = oriented_king // 8
    assert 4 <= file_index <= 7
    return (7 - rank_index) * 4 + (7 - file_index)


def test_contract_is_explicitly_provisional_and_reserves_only_v3_version() -> None:
    contract = _load_contract()
    assert contract["schema_version"] == 1
    assert contract["schema_id"] == "atomic-nnue-v3-prototype-v1"
    assert contract["contract_status"] == "provisional"
    assert contract["variant"] == "atomic"
    assert contract["backend"] == "AtomicNNUEV3"
    assert _u32(contract["file_version"]) == 0xA70C0003

    v2 = json.loads((ROOT / "schemas/atomic-nnue-v2.json").read_text(encoding="utf-8"))
    prior_versions = {
        _u32(backend["file_version"]) for backend in v2["backends"].values()
    }
    assert _u32(contract["file_version"]) not in prior_versions

    workflow = (ROOT / ".github/workflows/atomic.yml").read_text(encoding="utf-8")
    assert "tests/python/test_atomic_nnue_v3_contract.py" in workflow


def test_slice_offsets_dimensions_dtypes_and_training_factorization() -> None:
    contract = _load_contract()
    slices = contract["feature_slices"]
    assert [item["id"] for item in slices] == [
        "half-ka-v2-atomic-hm",
        "atomic-capture-pair",
        "atomic-king-blast-ep",
        "atomic-blast-ring",
    ]
    assert [item["physical_offset"] for item in slices] == [0, 22528, 69176, 71480]
    assert [item["physical_dimensions"] for item in slices] == [
        22528,
        46648,
        2304,
        10240,
    ]
    assert [item["weight_dtype"] for item in slices] == ["i16", "i8", "i16", "i8"]
    assert [item["psqt"] for item in slices] == [True, False, False, False]

    for current, following in zip(slices, slices[1:]):
        assert current["physical_offset"] + current["physical_dimensions"] == following[
            "physical_offset"
        ]

    dimensions = contract["dimensions"]
    assert sum(item["physical_dimensions"] for item in slices) == dimensions[
        "physical_total"
    ] == 81720
    assert sum(item["training_dimensions"] for item in slices) == dimensions[
        "training_total_excluding_virtual"
    ] == 83768
    assert sum(item["virtual_factor_dimensions"] for item in slices) == dimensions[
        "virtual_factor_total"
    ] == 768
    assert 83768 + 768 == dimensions["training_parameter_rows_total"] == 84536

    hm = slices[0]
    assert hm["king_buckets"] * hm["physical_piece_planes"] * 64 == 22528
    assert hm["king_buckets"] * hm["training_piece_planes"] * 64 == 24576


def test_active_feature_bounds_are_machine_readable_and_capacity_safe() -> None:
    contract = _load_contract()
    bounds = contract["active_feature_bounds"]
    assert bounds["scope"] == {
        "position_class": "evaluable-nonterminal",
        "required_kings": 2,
        "maximum_pieces_per_color": 16,
        "maximum_board_pieces": 32,
        "terminal_guard": (
            "a king-absent terminal is resolved before NNUE feature enumeration"
        ),
    }
    per_slice = bounds["per_perspective"]
    hm = per_slice["half_ka_v2_atomic_hm"]
    capture = per_slice["atomic_capture_pair"]
    king = per_slice["atomic_king_blast_ep"]
    ring = per_slice["atomic_blast_ring"]
    assert hm["classification"] == "tight-maximum"
    assert capture["classification"] == "conservative-upper-bound"
    assert king["classification"] == "conservative-upper-bound"
    assert ring["classification"] == "conservative-upper-bound"

    results = {
        "half_ka_v2_atomic_hm": (
            hm["components"]["maximum_board_pieces"]
            * hm["components"]["active_features_per_piece"]
        ),
        "atomic_capture_pair": (
            capture["components"]["maximum_non_king_actors"]
            * capture["components"]["maximum_candidate_edges_per_actor"]
        ),
        "atomic_king_blast_ep": (
            king["components"]["actor_relations"]
            * (
                king["components"]["enemy_king_center_or_neighbors_per_relation"]
                + king["components"]["own_king_neighbors_per_relation"]
            )
            + king["components"]["maximum_en_passant_markers"]
        ),
        "atomic_blast_ring": (
            ring["components"]["maximum_non_king_collateral_pieces"]
            * ring["components"]["maximum_adjacent_centers_per_collateral"]
        ),
    }
    assert results == {
        "half_ka_v2_atomic_hm": 32,
        "atomic_capture_pair": 240,
        "atomic_king_blast_ep": 35,
        "atomic_blast_ring": 240,
    }
    assert {
        item["id"]: item["max_active_reachable_material"]
        for item in contract["feature_slices"]
    } == results
    aggregate = bounds["aggregate"]
    assert aggregate["classification"] == "conservative-independent-slice-sum"
    assert aggregate["activation_domain"] == "physical/runtime-export"
    assert aggregate["components"] == results
    assert sum(results.values()) == aggregate["result"] == 547
    assert aggregate["physical_aggregate"] == 547
    assert aggregate["hm_virtual_expansion"] == 32
    assert (
        aggregate["physical_aggregate"] + aggregate["hm_virtual_expansion"]
        == aggregate["training_parameter_row"]
        == 579
    )
    assert aggregate["accumulator_capacity"] == 1024
    assert 1024 - aggregate["result"] == aggregate["capacity_headroom"] == 477
    assert contract["dimensions"]["max_active_reachable_material"] == 547
    assert (
        contract["dimensions"]["max_active_factorized_training_parameter_rows"]
        == 579
    )


def test_hm_orientation_plane_order_buckets_and_king_merge_are_unambiguous() -> None:
    contract = _load_contract()
    hm = _slice(contract, "half-ka-v2-atomic-hm")
    assert contract["orientation"]["square_ordinal"].startswith("A1=0, B1=1")
    assert hm["training_plane_order"] == [
        "OWN_PAWN",
        "OPP_PAWN",
        "OWN_KNIGHT",
        "OPP_KNIGHT",
        "OWN_BISHOP",
        "OPP_BISHOP",
        "OWN_ROOK",
        "OPP_ROOK",
        "OWN_QUEEN",
        "OPP_QUEEN",
        "OWN_KING",
        "OPP_KING",
    ]
    assert hm["physical_plane_order"] == hm["training_plane_order"][:10] + [
        "MERGED_KING"
    ]
    ordered_squares = [
        rank * 8 + file
        for rank in range(7, -1, -1)
        for file in range(7, 3, -1)
    ]
    assert [_king_bucket(square) for square in ordered_squares] == list(range(32))

    assert _orient_square("WHITE", 4, 8) == 8  # Ke1, a2 remains a2.
    assert _orient_square("WHITE", 3, 8) == 15  # Kd1 mirrors a2 to h2.
    assert _orient_square("BLACK", 60, 48) == 8  # ...Ke8, black a7 becomes own a2.
    assert "may therefore use different horizontal mirrors" in contract["orientation"][
        "perspective_independence"
    ]
    # The same Kd1/Ke8 position uses opposite horizontal branches for WHITE/BLACK.
    assert _orient_square("WHITE", 3, 8) == 15
    assert _orient_square("BLACK", 60, 48) == 8
    assert "shared by every slice" in contract["orientation"]["scope"]
    assert "never apply another square transform" in contract["orientation"][
        "slice_consistency"
    ]
    assert "initialize MERGED_KING from OPP_KING" in hm["king_plane_merge"]
    assert hm["coalesced_output_formula"].endswith("for output 0-1031")
    assert "all 8 PSQT outputs" in hm["export_order"]
    assert "accumulator outputs 0-1023 and PSQT outputs 1024-1031" in hm[
        "king_plane_merge_scope"
    ]


def test_capture_pair_geometry_is_derived_instead_of_magic() -> None:
    contract = _load_contract()
    capture = _slice(contract, "atomic-capture-pair")

    pawn = sum(
        1
        for rank in range(1, 7)
        for file in range(8)
        for df in (-1, 1)
        if 0 <= file + df < 8
    )
    knight = _leaper_edges(
        ((1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1), (-1, 2))
    )
    bishop = _slider_edges(((1, 1), (1, -1), (-1, -1), (-1, 1)))
    rook = _slider_edges(((1, 0), (0, -1), (-1, 0), (0, 1)))
    queen = bishop + rook

    counts = [entry["count"] for entry in capture["geometry_order"]]
    assert [pawn, knight, bishop, rook, queen] == counts == [84, 336, 560, 896, 1456]
    assert sum(counts) == capture["geometry_dimensions"] == 3332
    assert capture["geometry_segment_bases"] == {
        "PAWN": 0,
        "KNIGHT": 84,
        "BISHOP": 420,
        "ROOK": 980,
        "QUEEN": 1876,
    }
    assert "ascending A1=0 ordinal" in capture["edge_ordinal_order"]
    assert 2 * 3332 * len(capture["target_class_order"]) == capture[
        "physical_dimensions"
    ] == 46648
    assert "select an 84-edge table by actor_rel" in capture[
        "pawn_edge_ordinal_formula"
    ]
    assert "OWN contains north-moving diagonals" in capture[
        "pawn_edge_ordinal_formula"
    ]
    assert "OPP contains south-moving diagonals" in capture[
        "pawn_edge_ordinal_formula"
    ]
    assert "without another square transform" in capture["pawn_edge_ordinal_formula"]
    assert capture["semantics"]["target"] == (
        "stop at the first occupied square and emit only when that occupant is enemy"
    )
    assert "geometric pawn attack" in capture["semantics"]["en_passant"]
    assert "do not filter pins" in capture["semantics"]["en_passant"]
    assert "sole candidate source" in capture["semantics"]["relation_source"]


def test_atomic_relation_prototype_dimensions_and_semantic_orders_are_explicit() -> None:
    contract = _load_contract()
    king = _slice(contract, "atomic-king-blast-ep")
    ring = _slice(contract, "atomic-blast-ring")

    compass = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    assert king["class_order"] == (
        ["ENEMY_KING_CENTER"]
        + [f"ENEMY_KING_{offset}" for offset in compass]
        + [f"OWN_KING_{offset}" for offset in compass]
        + ["EN_PASSANT_MARKER"]
    )
    assert 64 * 2 * len(king["class_order"]) == king["physical_dimensions"] == 2304
    assert king["semantics"]["activation"].startswith("boolean set")
    assert "therefore illegal" in king["semantics"]["simultaneous_king_blast"]
    assert "encoded by HM" in king["semantics"]["touching_kings"]
    assert king["king_relation_frame"].endswith(
        "relative to the capture actor, not the accumulator perspective"
    )
    assert contract["orientation"]["direction_deltas_after_orientation"]["N"] == 8
    assert "oriented_related_king_square = oriented_center +" in king[
        "spatial_relation_formula"
    ]
    assert 27 + contract["orientation"]["direction_deltas_after_orientation"]["N"] == 35
    assert "never rotates or mirrors squares again" in king["spatial_frame"]
    assert "including geometric EN_PASSANT" in king["semantics"]["candidate_source"]

    assert ring["offset_order"] == compass
    assert ring["class_order"] == [
        "KNIGHT",
        "BISHOP",
        "ROOK",
        "QUEEN",
        "ADJACENT_PAWN_SURVIVES",
    ]
    assert 64 * 2 * 2 * 8 * 5 == ring["physical_dimensions"] == 10240
    assert "exact CapturePair candidate set" in ring["semantics"]["capturer_origin"]
    assert "excluded only when it is the sole origin" in ring["semantics"][
        "capturer_origin"
    ]
    assert "exclude oriented_center" in ring["semantics"][
        "en_passant_captured_pawn"
    ]
    assert ring["collateral_relation_frame"] == (
        "relative to the accumulator perspective, not the capture actor"
    )
    assert "oriented_collateral_square = oriented_center +" in ring[
        "spatial_relation_formula"
    ]
    assert "never rotate or mirror squares again" in ring["spatial_frame"]
    assert "including geometric EN_PASSANT" in ring["semantics"]["candidate_source"]


def test_wire_keeps_v2_tail_but_makes_mixed_slices_unambiguous() -> None:
    contract = _load_contract()
    wire = contract["wire"]
    assert wire["order"] == [
        "file_version:u32",
        "network_hash:u32",
        "description_length:u32",
        "description:bytes",
        "feature_transformer_hash:u32",
        "biases:i16-sleb128",
        "half_ka_v2_atomic_hm_weights:i16-sleb128",
        "atomic_capture_pair_weights:i8-raw",
        "atomic_king_blast_ep_weights:i16-sleb128",
        "atomic_blast_ring_weights:i8-raw",
        "half_ka_v2_atomic_hm_psqt:i32-sleb128",
        "eight_times_architecture_hash_and_sfnnv15_stack",
        "strict_eof",
    ]
    assert wire["psqt_scope"] == "HalfKAv2Atomic_hm only"
    assert wire["tensor_shapes"]["half_ka_v2_atomic_hm_psqt"] == {
        "dtype": "i32",
        "shape": [22528, 8],
    }
    assert "8 PSQT buckets contiguous" in wire["tensor_order"]
    assert "same virtual-factor coalesce" in wire["psqt_export_mapping"]
    assert wire["dense_tail"] == (
        "exactly the top-level dense_tail object, copied from atomic-nnue-v2"
    )
    assert wire["sleb128_framing"] == (
        "COMPRESSED_LEB128 followed by a little-endian u32 compressed byte count "
        "and canonical signed values"
    )
    assert _u32(contract["hashing"]["architecture_hash"]) == 0x63337116

    v2 = json.loads((ROOT / "schemas/atomic-nnue-v2.json").read_text(encoding="utf-8"))[
        "backends"
    ]["atomic-nnue-v2"]
    dense_tail = contract["dense_tail"]
    assert dense_tail["pairwise_multiply"] == v2["pairwise_multiply"]
    assert dense_tail["topology"] == v2["topology"]
    assert _u32(dense_tail["architecture_hash"]) == _u32(v2["architecture_hash"])
    assert contract["dimensions"]["accumulator_dimensions_per_perspective"] == v2[
        "accumulator_dimensions_per_perspective"
    ]
    assert contract["dimensions"]["psqt_buckets"] == v2["psqt_buckets"]
    assert contract["dimensions"]["layer_stacks"] == v2["layer_stacks"]
    bucket = contract["dense_tail"]["bucket_selection"]
    assert bucket["formula"] == "clamp(integer_divide(piece_count - 1, 4), 0, 7)"
    assert "same single bucket" in bucket["shared_selection"]
    assert bucket["feature_input_identity"].endswith("exactly once as bucket_u8")

    trainer = contract["trainer_policy"]
    assert trainer["hm_outputs"] == (
        "1024 accumulator columns plus 8 trainable PSQT columns"
    )
    assert trainer["relation_outputs"] == (
        "1024 accumulator columns and no PSQT parameters"
    )
    assert trainer["relation_psqt"] == "absent, not zero-initialized trainable columns"
    assert "not wire-compatible" in trainer["feature_transformer"]


def test_hash_recipe_is_deterministic_but_production_values_remain_unfrozen() -> None:
    contract = _load_contract()
    hashing = contract["hashing"]
    slices = {item["id"]: item for item in contract["feature_slices"]}

    assert hashing["status"] == "not frozen"
    assert hashing["descriptor_encoding"] == "ASCII"
    assert _u32(hashing["fnv_offset_basis"]) == 0x811C9DC5
    assert _u32(hashing["fnv_prime"]) == 0x01000193
    assert _fnv1a32(b"hello") == 0x4F9F2CAB
    assert hashing["feature_fold_order"] == list(slices)
    provisional_slice_hashes = [
        _fnv1a32(slices[slice_id]["descriptor"].encode("ascii"))
        for slice_id in hashing["feature_fold_order"]
    ]
    provisional_feature_hash = _fold_hashes(provisional_slice_hashes)
    transformer_descriptor_hash = _fnv1a32(
        hashing["transformer_descriptor"].encode("ascii")
    )
    provisional_transformer_hash = (
        provisional_feature_hash ^ 2048 ^ transformer_descriptor_hash
    )
    assert provisional_feature_hash != 0
    assert provisional_feature_hash == _fold_hashes(provisional_slice_hashes)
    assert provisional_transformer_hash != 0
    assert hashing["feature_transformer_hash"] == (
        "feature_hash XOR 2048 XOR transformer_descriptor_hash"
    )
    assert hashing["feature_hash_value"] is None
    assert hashing["transformer_descriptor_hash_value"] is None
    assert hashing["feature_transformer_hash_value"] is None
    assert hashing["network_hash_value"] is None
    assert hashing["semantic_decision_blockers"] == [
        "per-perspective joint orientation golden semantics",
        "HM virtual-factor coalesce and 12-to-11 accumulator and PSQT export goldens",
        "AtomicCapturePair edge ordinals and geometric en-passant goldens",
        "AtomicKingBlastEP center-to-king offset and en-passant goldens",
        "AtomicBlastRing center-to-collateral, multiple-origin, pawn-immunity and en-passant goldens",
        "HM-only versus relation PSQT ablation decision",
        "runtime accumulator arithmetic bound",
    ]
    assert hashing["freeze_gates"] == [
        "all semantic decision blockers resolved",
        "golden indices for every class, edge, perspective, independent WHITE/BLACK mirror branch, center-to-related-square direction and HM factorized 12-to-11 accumulator and PSQT export mapping",
        "mixed-wire canonical read, write, corruption and byte-exact round trip fixture",
        "bit-exact C++ and Python descriptors, dimensions, offsets and hash generation",
        "runtime loader still rejects V3 until every numeric hash is non-null and tested",
    ]

    dispatcher_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "src/nnue/nnue_dispatcher.cpp",
            ROOT / "src/nnue/network.cpp",
            ROOT / "src/nnue/network.h",
        )
    )
    assert "0xA70C0003" not in dispatcher_sources
    assert "AtomicNNUEV3" not in dispatcher_sources


def test_dataset_statistics_schema_authenticates_coverage_and_leakage_gates() -> None:
    schema = _load_lf_json(STATS_SCHEMA_FILE)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert len(schema["x-semantic-invariants"]) >= 22
    assert "WHITE/BLACK coverage is never aggregated" in schema["description"]
    assert any(
        "structurally_reachable + structurally_unreachable" in rule
        for rule in schema["x-semantic-invariants"]
    )
    assert set(schema["required"]) == {
        "schema_version",
        "role",
        "provenance",
        "artifacts",
        "backend",
        "scanner",
        "scan",
        "split",
        "distribution",
        "coverage_by_perspective",
        "record_events",
        "trajectory_events",
        "deduplication",
    }

    definitions = schema["$defs"]
    assert definitions["count"]["$ref"] == "#/$defs/uint64"
    assert definitions["uint64"]["type"] == "string"
    assert set(definitions["artifacts"]["required"]) == {
        "atomic_bin_v2_manifest",
        "trajectory_ledger",
        "index_coverage",
        "coverage_policy",
    }
    assert definitions["scanner"]["properties"]["oracle"]["const"] == (
        "independent-i32-full-refresh"
    )
    scan = definitions["scan"]["properties"]
    assert scan["mode"]["const"] == "full"
    assert scan["invalid_records"]["const"] == "0"
    assert scan["truncated_active_lists"]["const"] == "0"
    assert scan["accumulator_overflows"]["const"] == "0"
    assert "all_ledger_entries_structurally_scanned" in definitions["scan"][
        "required"
    ]
    assert "all_ledger_entries_replayed" not in json.dumps(schema)
    assert scan["all_ledger_entries_structurally_scanned"]["const"] is True
    assert scan["max_active_observed"]["properties"]["WHITE"]["maximum"] == 547
    assert scan["max_active_observed"]["properties"]["BLACK"]["maximum"] == 547

    split = definitions["split"]
    assert split["properties"]["method"]["const"] == "content-hash-trajectory-v1"
    assert "partition_config_sha256" in split["required"]
    assert "validation_threshold_u64" in split["required"]
    assert set(definitions["backend"]["required"]) >= {
        "reachability_mask_sha256",
        "reachability_masks",
    }

    perspective = definitions["perspectiveCoverage"]
    expected_dimensions = {
        "half_ka_v2_atomic_hm": 22528,
        "atomic_capture_pair": 46648,
        "atomic_king_blast_ep": 2304,
        "atomic_blast_ring": 10240,
    }
    expected_active_maxima = {
        "half_ka_v2_atomic_hm": 32,
        "atomic_capture_pair": 240,
        "atomic_king_blast_ep": 35,
        "atomic_blast_ring": 240,
    }
    for name, dimensions in expected_dimensions.items():
        specialization = perspective["properties"][name]["allOf"][1]["properties"]
        assert specialization["physical_dimensions"]["const"] == dimensions
        active_ref = specialization["active_per_position"]["$ref"]
        active_definition = definitions[active_ref.removeprefix("#/$defs/")]
        bounded_properties = active_definition["allOf"][1]["properties"]
        for statistic in ("minimum", "maximum", "p50", "p95", "p99"):
            assert bounded_properties[statistic]["maximum"] == expected_active_maxima[
                name
            ]
    active_contract = schema["x-active-count-contract"]
    assert active_contract["slice_maxima"] == expected_active_maxima
    assert active_contract["aggregate_maximum"] == sum(expected_active_maxima.values())
    assert active_contract["aggregate_maximum"] == 547
    assert active_contract["accumulator_capacity"] == 1024
    assert active_contract["capacity_headroom"] == 477
    assert active_contract["activation_domain"] == "physical/runtime-export"
    assert "579 parameter rows" in active_contract["factorized_training_note"]
    assert active_contract["source"] == "schemas/atomic-nnue-v3.json#active_feature_bounds"
    assert "16 pieces per color" in active_contract["scope"]
    assert "max_per_record(sum(active slice counts))" in active_contract["linkage"]
    hm_training = definitions["hmTrainingCoverage"]["properties"]
    assert hm_training["training_dimensions"]["const"] == 24576
    assert hm_training["virtual_factor_dimensions"]["const"] == 768

    assert definitions["capturePairClasses"]["minItems"] == 2
    assert definitions["capturePairClasses"]["items"]["minItems"] == 5
    assert definitions["capturePairClasses"]["items"]["items"]["$ref"] == (
        "#/$defs/counts7"
    )
    king_coverage = definitions["kingBlastClasses"]["properties"]
    assert king_coverage["by_actor_class"]["minItems"] == 2
    assert king_coverage["by_actor_class"]["items"]["$ref"] == (
        "#/$defs/counts18"
    )
    ring_coverage = definitions["blastRingClasses"]["properties"]
    ring_tensor = ring_coverage["by_actor_collateral_offset_class"]
    assert ring_tensor["minItems"] == 2
    assert ring_tensor["items"]["minItems"] == 2
    assert ring_tensor["items"]["items"]["minItems"] == 8
    assert ring_tensor["items"]["items"]["items"]["$ref"] == (
        "#/$defs/counts5"
    )
    semantic = set(definitions["semanticCounters"]["required"])
    assert {
        "ep_adjacent_enemy_king",
        "ep_adjacent_own_king",
        "simultaneous_enemy_and_own_blast",
        "touching_kings",
        "sole_origin_excluded",
        "multiple_origin_preserved",
        "off_center_ep_pawn_excluded",
    } <= semantic
    assert "terminal_positions" not in json.dumps(schema)
    assert definitions["trajectoryEvents"]["properties"]["stop_reasons"][
        "$ref"
    ] == "#/$defs/counts9"
    assert schema["x-stop-reason-release-contract"][
        "release_candidate_required_zero_indices"
    ] == [7, 8]
    assert any(
        "sum(WHITE.hm_king_buckets)" in rule
        and "sum(distribution.network_buckets)" in rule
        for rule in schema["x-semantic-invariants"]
    )
    distribution = definitions["distribution"]
    assert "network_buckets" in distribution["required"]
    assert "psqt_buckets" not in distribution["properties"]
    assert "layer_stacks" not in distribution["properties"]


def test_trajectory_ledger_wire_is_label_free_replayable_and_contiguous() -> None:
    ledger = _load_lf_json(LEDGER_SCHEMA_FILE)
    assert ledger["schema_id"] == "atomic-trajectory-ledger-v1"
    assert ledger["header"]["size"] == 160
    assert ledger["entry"]["size"] == 112
    _assert_contiguous_fields(ledger["header"]["fields"], 160)
    _assert_contiguous_fields(ledger["entry"]["fields"], 112)

    header_names = [field["name"] for field in ledger["header"]["fields"]]
    assert header_names == [
        "magic",
        "version",
        "header_size",
        "endian_marker",
        "entry_size",
        "role",
        "schema_sha256",
        "dataset_manifest_sha256",
        "data_schema_sha256",
        "record_count",
        "trajectory_count",
        "move_count",
        "entries_offset",
        "moves_offset",
    ]
    moves_offset = next(
        field for field in ledger["header"]["fields"] if field["name"] == "moves_offset"
    )
    assert moves_offset["formula"] == "160 + trajectory_count * 112"
    assert ledger["file_policy"]["file_size_formula"] == (
        "160 + trajectory_count * 112 + move_count * 4"
    )
    assert bytes.fromhex(ledger["split_group_id"]["domain_ascii_hex"]) == (
        b"atomic-split-group-v1\0"
    )
    group_formula = ledger["split_group_id"]["formula"]
    assert "root_position[48]" in group_formula
    assert "complete_move_wires_u32_le" in group_formula
    assert "terminal_result" not in group_formula
    assert "stop_reason" not in group_formula
    assert "label" in ledger["split_group_id"]["label_free"]

    stop_reason = next(
        field for field in ledger["entry"]["fields"] if field["name"] == "stop_reason"
    )
    assert stop_reason["mapping"] == {
        "atomic-explosion": 0,
        "checkmate": 1,
        "stalemate": 2,
        "insufficient-material": 3,
        "fifty-move-rule": 4,
        "threefold-repetition": 5,
        "maximum-ply-draw": 6,
        "score-draw-adjudication": 7,
        "evaluation-resignation": 8,
    }
    assert "strictly increasing" in ledger["verification"]["position_replay"]
    assert "may differ" in ledger["verification"]["move_semantics"]
    assert ledger["verification"]["strict_eof"] is True


def test_index_coverage_wire_has_exact_v3_segments_and_no_float_counters() -> None:
    coverage = _load_lf_json(INDEX_COVERAGE_SCHEMA_FILE)
    assert coverage["header"]["size"] == 128
    _assert_contiguous_fields(coverage["header"]["fields"], 128)
    assert coverage["counter"]["storage"] == "uint64"
    assert coverage["counter"]["size"] == 8

    segments = coverage["segments"]
    expected_segments = [
        (perspective, kind, slice_id, count)
        for perspective in ("WHITE", "BLACK")
        for kind, slice_id, count in (
            ("physical", "half-ka-v2-atomic-hm", 22528),
            ("physical", "atomic-capture-pair", 46648),
            ("physical", "atomic-king-blast-ep", 2304),
            ("physical", "atomic-blast-ring", 10240),
            ("training", "half-ka-v2-atomic-hm", 24576),
            ("virtual-factor", "half-ka-v2-atomic-hm", 768),
        )
    ]
    assert [
        (item["perspective"], item["kind"], item["slice"], item["count"])
        for item in segments
    ] == expected_segments
    next_offset = 0
    for segment in segments:
        assert segment["offset"] == next_offset
        next_offset += segment["count"]
    assert next_offset == 214128
    assert len(segments) == 12
    assert [segment["perspective"] for segment in segments[:6]] == ["WHITE"] * 6
    assert [segment["perspective"] for segment in segments[6:]] == ["BLACK"] * 6
    assert [segment["count"] for segment in segments[:6]] == [
        22528,
        46648,
        2304,
        10240,
        24576,
        768,
    ]
    counter_count = next(
        field
        for field in coverage["header"]["fields"]
        if field["name"] == "counter_count"
    )
    assert counter_count["required_value"] == next_offset
    mask_bytes = 2 * (2816 + 5831 + 288 + 1280 + 3072 + 96)
    assert mask_bytes == 26766
    assert coverage["reachability_masks"]["storage"] == (
        "trailing-canonical-bitmaps"
    )
    assert "dataset counters and labels are forbidden inputs" in coverage[
        "reachability_masks"
    ]["derivation"]
    assert len(coverage["reachability_masks"]["layout"]) == 12
    reachability = coverage["reachability_masks"]
    assert reachability["kind_id"] == {
        "physical": 0,
        "training": 1,
        "virtual-factor": 2,
    }
    assert bytes.fromhex(reachability["per_mask_hash"]["domain_ascii_hex"]) == (
        b"atomic-v3-reachability-mask-v2\0"
    )
    assert bytes.fromhex(reachability["aggregate_hash"]["domain_ascii_hex"]) == (
        b"atomic-v3-reachability-set-v2\0"
    )
    assert reachability["aggregate_hash"]["per_mask_digest_order"] == [
        "WHITE.physical.half-ka-v2-atomic-hm",
        "WHITE.physical.atomic-capture-pair",
        "WHITE.physical.atomic-king-blast-ep",
        "WHITE.physical.atomic-blast-ring",
        "WHITE.training.half-ka-v2-atomic-hm",
        "WHITE.virtual-factor.half-ka-v2-atomic-hm",
        "BLACK.physical.half-ka-v2-atomic-hm",
        "BLACK.physical.atomic-capture-pair",
        "BLACK.physical.atomic-king-blast-ep",
        "BLACK.physical.atomic-blast-ring",
        "BLACK.training.half-ka-v2-atomic-hm",
        "BLACK.virtual-factor.half-ka-v2-atomic-hm",
    ]
    assert coverage["file_policy"]["file_size"] == 128 + 214128 * 8 + mask_bytes
    assert coverage["file_policy"]["formula"] == "128 + 214128 * 8 + 26766"


def test_coverage_policy_and_split_audit_are_hash_pinned_integer_gates() -> None:
    policy = _load_lf_json(COVERAGE_POLICY_SCHEMA_FILE)
    stats = _load_lf_json(STATS_SCHEMA_FILE)
    audit = _load_lf_json(SPLIT_AUDIT_SCHEMA_FILE)
    assert policy["additionalProperties"] is False
    assert "partition" in policy["required"]
    partition = policy["$defs"]["partitionConfig"]
    assert set(partition["required"]) == {
        "config_sha256",
        "method",
        "split_seed",
        "validation_threshold_u64",
        "provenance",
    }
    assert policy["properties"]["train"]["$ref"] == "#/$defs/partitionGate"
    assert policy["properties"]["validation"]["$ref"] == (
        "#/$defs/partitionGate"
    )
    assert policy["$defs"]["ppm"]["maximum"] == 1000000
    assert policy["$defs"]["uint64"]["type"] == "string"
    assert policy["$defs"]["globalGates"]["properties"][
        "active_feature_capacity"
    ]["const"] == 1024
    assert "strictly-increasing" in policy["$defs"][
        "strictlyIncreasingIntArray"
    ]["x-order"]
    histogram_boundaries = policy["$defs"]["histogramBoundaries"]["properties"]
    assert histogram_boundaries["ply"]["$ref"] == (
        "#/$defs/nonNegativeStrictlyIncreasingIntArray"
    )
    assert histogram_boundaries["rule50"]["$ref"] == (
        "#/$defs/nonNegativeStrictlyIncreasingIntArray"
    )
    piece_count_items = policy["$defs"][
        "pieceCountStrictlyIncreasingIntArray"
    ]["items"]
    assert histogram_boundaries["piece_count"]["$ref"] == (
        "#/$defs/pieceCountStrictlyIncreasingIntArray"
    )
    assert piece_count_items["minimum"] == 0
    assert piece_count_items["maximum"] == 32
    expected_mask_fields = (
        "{half_ka_v2_atomic_hm,atomic_capture_pair,atomic_king_blast_ep,"
        "atomic_blast_ring,hm_training,hm_virtual_factors}"
    )
    assert policy["x-reachability-mask-binding"]["per_mask_fields"] == (
        "reachability_masks.{WHITE,BLACK}." + expected_mask_fields
    )
    assert stats["x-reachability-mask-binding"]["per_mask_field"] == (
        "backend.reachability_masks.{WHITE,BLACK}." + expected_mask_fields
    )

    identities = audit["$defs"]["identityDefinitions"]["properties"]
    assert "raw-atomic-bin-v2-record-64" in identities["raw_record_key"]["const"]
    feature_key = identities["feature_input_key"]["const"]
    assert "feature-schema-sha256-32" in feature_key
    assert "side-to-move-u8" not in feature_key
    assert "sorted-stm-physical-indices" in feature_key
    assert "sorted-opponent-physical-indices" in feature_key
    assert "bucket-u8" in feature_key
    assert "psqt-bucket-u8" not in feature_key
    assert "layer-stack-u8" not in feature_key
    assert audit["properties"]["intersections"]["properties"] == {
        "raw_record_keys": {"const": "0"},
        "feature_input_keys": {"const": "0"},
        "split_group_ids": {"const": "0"},
    }
    verification = audit["$defs"]["verification"]["properties"]
    assert all(item["const"] is True for item in verification.values())
    assert "full_ledger_structural_scans" in audit["$defs"]["verification"][
        "required"
    ]
    assert "full_ledger_replays" not in json.dumps(audit)
    assert set(audit["$defs"]["partitionBinding"]["required"]) == set(
        partition["required"]
    )
    assert verification["same_split_seed"]["const"] is True
    assert verification["same_validation_threshold"]["const"] is True
    assert verification["same_generation_provenance"]["const"] is True
    assert verification["same_reachability_masks"]["const"] is True


def test_training_run_manifest_is_acyclic_root_of_trust() -> None:
    manifest = _load_lf_json(TRAINING_RUN_SCHEMA_FILE)
    assert manifest["additionalProperties"] is False
    assert manifest["properties"]["status"]["const"] == "completed"
    inputs = manifest["$defs"]["inputs"]
    assert set(inputs["required"]) == {
        "feature_schema",
        "dataset_schema",
        "manifest_schema",
        "trajectory_ledger_schema",
        "index_coverage_schema",
        "statistics_schema",
        "coverage_policy_schema",
        "split_audit_schema",
        "train",
        "validation",
        "coverage_policy",
        "split_audit",
    }
    assert bytes.fromhex(manifest["x-run-definition"]["domain_ascii_hex"]) == (
        b"atomic-v3-run-definition-v1\0"
    )
    assert bytes.fromhex(manifest["x-input-bundle"]["domain_ascii_hex"]) == (
        b"atomic-v3-input-bundle-v1\0"
    )
    assert manifest["x-input-bundle"]["formula"] == (
        "SHA256(domain || eighteen-input-artifact-sha256-values-as-raw32-in-declared-order || run_definition_sha256_raw32)"
    )
    assert manifest["x-input-bundle"]["component_order"][-1] == (
        "run_definition_sha256_raw32"
    )
    assert "input_bundle_sha256" in manifest["x-input-bundle"][
        "checkpoint_fields"
    ]
    assert "run_definition_sha256" in manifest["x-input-bundle"][
        "checkpoint_fields"
    ]
    assert "run_definition_sha256" in manifest["required"]
    assert "checkpoint" not in inputs["properties"]
    schedule = manifest["$defs"]["schedule"]["properties"]
    assert schedule["optimizer_steps"]["$ref"] == "#/$defs/positiveUint64"
    assert schedule["validation_interval_steps"]["$ref"] == (
        "#/$defs/positiveUint64"
    )
    assert manifest["$defs"]["positiveUint64"]["pattern"].startswith("^[1-9]")
    outputs = manifest["$defs"]["outputs"]["properties"]
    assert outputs["checkpoint"]["$ref"] == "#/$defs/nonEmptyArtifact"
    assert outputs["network"]["$ref"] == "#/$defs/nonEmptyArtifact"
    assert manifest["$defs"]["nonEmptyArtifact"]["allOf"][1]["properties"][
        "bytes"
    ]["$ref"] == "#/$defs/positiveUint64"
    verification = manifest["$defs"]["verification"]["properties"]
    assert verification["checkpoint_input_bundle_matches"]["const"] is True
    assert verification["network_engine_load_passed"]["const"] is True

    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for path in (
        STATS_SCHEMA_FILE,
        LEDGER_SCHEMA_FILE,
        INDEX_COVERAGE_SCHEMA_FILE,
        COVERAGE_POLICY_SCHEMA_FILE,
        SPLIT_AUDIT_SCHEMA_FILE,
        TRAINING_RUN_SCHEMA_FILE,
    ):
        assert path.relative_to(ROOT).as_posix() in attributes


def test_numeric_and_null_move_policies_fail_safe() -> None:
    contract = _load_contract()
    numeric = contract["numeric_policy"]
    null_move = contract["null_move_policy"]

    assert numeric["oracle_accumulator_dtype"] == "i32"
    assert numeric["runtime_accumulator_policy"] == "unfrozen"
    assert "never saturate or wrap" in numeric["overflow_policy"]
    assert contract["dimensions"]["full_refresh_enumerator"].startswith(
        "streaming visitor"
    )
    assert "epSquareWhenComputed" in null_move["guard"]
    assert null_move["required_fixture"] == (
        "EP parent -> evaluate -> null move -> evaluate -> undo null -> evaluate parent"
    )
    assert null_move["required_fixture_assertions"] == [
        "the EP parent has both perspectives computed before the null move",
        "the null move does not push the NNUE accumulator and reaches the guard with computed state",
        "the EP mismatch invalidates and refreshes relation inputs for both perspectives before any computed early return",
        "the HM Finny cache records a real hit for both perspectives while relation slices refresh",
        "undo null changes the EP key back and forces a second relation refresh for both perspectives",
        "the final parent accumulators, PSQT, transformed bytes and raw output are bit-identical to their pre-null values",
    ]
