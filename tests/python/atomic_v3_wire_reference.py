#!/usr/bin/env python3
"""Independent streaming parser for the frozen AtomicNNUEV3 wire-v1.

The descriptor byte strings below are deliberately duplicated rather than
loaded from the JSON schema or from engine sources.  This module is therefore
an independent hash and wire oracle.  Large tensors are validated in bounded
chunks; only requested scalar values, PSQT top-32 heaps, and dense bounds are
retained.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
import hashlib
import heapq
from pathlib import Path
import struct
from typing import BinaryIO, Callable, Iterable, Mapping, Optional


FILE_VERSION = 0xA70C0003
ARCHITECTURE_HASH = 0x63337116
FNV_OFFSET_BASIS = 0x811C9DC5
FNV_PRIME = 0x01000193
LEB128_MAGIC = b"COMPRESSED_LEB128"

HM_DESCRIPTOR = (
    b"HalfKAv2Atomic_hm|v1|square=A1_0_rank_major|offset=0|axes=king_bucket_h8_to_e1:32,"
    b"piece_plane:OWN_P,OPP_P,OWN_N,OPP_N,OWN_B,OPP_B,OWN_R,OPP_R,OWN_Q,OPP_Q,MERGED_K,"
    b"square:64|physical=22528|training_planes=OWN_P,OPP_P,OWN_N,OPP_N,OWN_B,OPP_B,OWN_R,"
    b"OPP_R,OWN_Q,OPP_Q,OWN_K,OPP_K|training=24576|virtual=768|factor=bucket_plus_virtual_"
    b"all_1032_then_export|king_merge=opp_then_own_king_square_all_1032|orientation=per_"
    b"perspective_black_xor56_mirror_if_pre_h_king_file_lt4_shared_all_slices|royal=KING|"
    b"dtype=i16|wire=i16_sleb_feature_major_output1024_contiguous_canonical_unpermuted_"
    b"permute16|psqt=hm_only_i32_sleb_feature_major_bucket8_contiguous_after_relations"
)
CAPTURE_PAIR_DESCRIPTOR = (
    b"AtomicCapturePair|v2-compact|square=A1_0_rank_major|offset=22528|axes=normal:actor_"
    b"rel_accumulator:OWN,OPP;edge:PAWN84@0,KNIGHT336@84,BISHOP560@420,ROOK896@980,"
    b"QUEEN1456@1876;target_enemy_of_actor:PAWN,KNIGHT,BISHOP,ROOK,QUEEN,KING;normal_local="
    b"((actor_rel*3332+edge)*6+target)@offset0_count39984;ep_tail=(actor_rel*14+ep_ordinal)"
    b"@offset39984_count28;ep_edges=OWN_rank5_to6_OPP_rank4_to3_oriented_from_then_center_"
    b"asc|physical=40012|physical_index=22528+local|orientation=per_perspective_black_xor56_"
    b"mirror_if_pre_h_king_file_lt4_shared_all_slices|actor_rel=color_only|pawn_edge=OWN_"
    b"north_OPP_south_no_extra_flip|occupancy=stop_first_occupied_emit_enemy|pins_checks_"
    b"self_blast=unfiltered|promotion=one_pawn_relation_no_choice_expansion_current_piece_"
    b"types|ep=validated_stm_geometric_cold_tail_fail_closed_source_for_kbr_ring|impossible_"
    b"ep_rows=eliminated_no_holes|order=local_asc_unique|ownership=caller_owned|thread=pure_"
    b"reentrant_immutable_position|king_actor=excluded|pawn_push=excluded|dtype=i8|wire=i8_"
    b"raw_signed_twos_feature_major_output1024_contiguous_canonical_unpermuted_permute8_"
    b"after_hm|psqt=none"
)
KING_BLAST_EP_DESCRIPTOR = (
    b"AtomicKingBlastEP|v1|offset=62540|axes=center:64;actor_rel_accumulator:OWN,OPP;king_rel_"
    b"actor:ENEMY_KING_CENTER,ENEMY_KING_N,ENEMY_KING_NE,ENEMY_KING_E,ENEMY_KING_SE,"
    b"ENEMY_KING_S,ENEMY_KING_SW,ENEMY_KING_W,ENEMY_KING_NW,OWN_KING_N,OWN_KING_NE,"
    b"OWN_KING_E,OWN_KING_SE,OWN_KING_S,OWN_KING_SW,OWN_KING_W,OWN_KING_NW;class:EN_"
    b"PASSANT_MARKER|local=((center*2+actor_rel)*18+class)@0..2303|physical=2304@62540.."
    b"64843|orientation=per_perspective_black_xor56_mirror_if_pre_h_king_file_lt4_shared_"
    b"all_slices|source=single_exact_unfiltered_cp_emission_including_validated_geometric_ep|"
    b"offset=related_king_minus_center_in_joint_frame_exact_dfdr|activation=boolean_sorted_"
    b"unique_capture_center_set|ep=landing_center_dedup_offcenter_pawn_excluded_fail_closed|"
    b"rectangle=full_no_holes|error=cp_mapped_empty_no_partial|ownership=caller_owned|thread="
    b"pure_reentrant_immutable_position|max=17x2_plus1_eq35|dtype=i16|wire=i16_sleb_feature_"
    b"major_output1024_contiguous_canonical_unpermuted_permute16_after_capture_pair|psqt=none"
)
BLAST_RING_DESCRIPTOR = (
    b"AtomicBlastRing|v1|offset=64844|axes=center:64;actor_rel_accumulator:OWN,OPP;collateral_"
    b"rel_accumulator:OWN,OPP;offset:N,NE,E,SE,S,SW,W,NW;class:KNIGHT,BISHOP,ROOK,QUEEN,"
    b"ADJACENT_PAWN_SURVIVES|local=((((center*2+actor_rel)*2+collateral_rel)*8+offset)*5+"
    b"class)@0..10239|physical=10240@64844..75083|orientation=per_perspective_black_xor56_"
    b"mirror_if_pre_h_king_file_lt4_shared_all_slices|source=single_exact_unfiltered_cp_"
    b"emission_including_validated_geometric_ep|group=center_actor_rel_distinct_origins|"
    b"offset=collateral_minus_center_in_joint_frame_exact_dfdr|activation=boolean_sorted_"
    b"unique_capture_center_union|origin=exclude_only_single_distinct_origin_group_retain_all_"
    b"origins_if_multi|nonpawn=current_NBRQ_explodes|pawn=adjacent_survives_except_single_"
    b"origin_or_ep_captured|ep=landing_center_malformed_omitted_normal_preserved|ep_captured_"
    b"pawn=oriented_center_minus_own8_or_opp_minus8_always_excluded_even_multi|kings=separate|"
    b"rectangle=full_no_holes|error=cp_mapped_empty_no_partial|ownership=caller_owned|thread="
    b"pure_reentrant_immutable_position|max=30x8_eq240|dtype=i8|wire=i8_raw_signed_twos_"
    b"feature_major_output1024_contiguous_canonical_unpermuted_permute8_after_king_blast|"
    b"psqt=none"
)
TRANSFORMER_DESCRIPTOR = (
    b"AtomicNNUEV3Transformer|v1|wire=biases:i16_sleb[1024],hm:i16_sleb[22528x1024],"
    b"cp:i8_raw[40012x1024],kbr:i16_sleb[2304x1024],ring:i8_raw[10240x1024],hm_psqt:"
    b"i32_sleb[22528x8],dense:8x(architecture_hash_u32=0x63337116,sfnnv15)|layout=each_"
    b"feature_slice_feature_major_output1024_contiguous;hm_psqt_feature_major_bucket8_"
    b"contiguous|sleb=COMPRESSED_LEB128_then_u32_le_byte_count_canonical_signed|file=canonical_"
    b"unpermuted|raw_i8=signed_twos_complement|load_permute=biases,hm,kbr:i16_block16;cp,"
    b"ring:i8_block8;hm_psqt:none|permute_order=avx512[0,2,4,6,1,3,5,7],avx2_lasx[0,2,1,"
    b"3,4,6,5,7],other[0,1,2,3,4,5,6,7]|save=unpermute_copy_inverse_order_no_live_"
    b"mutation|psqt=hm_only_same_virtual_factor_coalesce_and_12to11_export|dense_tail=byte_"
    b"identical_atomic_v2_sfnnv15_architecture_0x63337116|strict_eof=true"
)


def fnv1a32(data: bytes) -> int:
    value = FNV_OFFSET_BASIS
    for byte in data:
        value = ((value ^ byte) * FNV_PRIME) & 0xFFFFFFFF
    return value


def rotate_left_one(value: int) -> int:
    return ((value << 1) | (value >> 31)) & 0xFFFFFFFF


SLICE_HASHES = tuple(
    fnv1a32(descriptor)
    for descriptor in (
        HM_DESCRIPTOR,
        CAPTURE_PAIR_DESCRIPTOR,
        KING_BLAST_EP_DESCRIPTOR,
        BLAST_RING_DESCRIPTOR,
    )
)
FEATURE_HASH = 0
for _slice_hash in SLICE_HASHES:
    FEATURE_HASH = rotate_left_one(FEATURE_HASH) ^ _slice_hash
TRANSFORMER_DESCRIPTOR_HASH = fnv1a32(TRANSFORMER_DESCRIPTOR)
FEATURE_TRANSFORMER_HASH = FEATURE_HASH ^ 2048 ^ TRANSFORMER_DESCRIPTOR_HASH
NETWORK_HASH = FEATURE_TRANSFORMER_HASH ^ ARCHITECTURE_HASH

EXPECTED_SLICE_HASHES = (0xA34A8666, 0x9AEDB186, 0xF5172BC0, 0x38377946)
EXPECTED_FEATURE_HASH = 0xA3FBDBE8
EXPECTED_TRANSFORMER_DESCRIPTOR_HASH = 0xCC31067A
EXPECTED_FEATURE_TRANSFORMER_HASH = 0x6FCAD592
EXPECTED_NETWORK_HASH = 0x0CF9A484

ACCUMULATOR_DIMENSIONS = 1024
HM_DIMENSIONS = 22528
CAPTURE_PAIR_DIMENSIONS = 40012
KING_BLAST_EP_DIMENSIONS = 2304
BLAST_RING_DIMENSIONS = 10240
PSQT_BUCKETS = 8
LAYER_STACKS = 8
FC0_INPUTS = 1024
FC0_OUTPUTS = 32
FC1_INPUTS = 64
FC1_OUTPUTS = 32
FC2_INPUTS = 128
INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1
FWD_MIN = -3665038760
FWD_MAX = 3665038759
MAX_DESCRIPTION_BYTES = 1 << 20
STREAM_CHUNK = 1 << 20


class WireError(ValueError):
    """The candidate is not one complete canonical AtomicNNUEV3 network."""


@dataclass(frozen=True)
class WireSpan:
    frame_offset: int
    payload_offset: int
    payload_size: int
    elements: int
    length_offset: Optional[int] = None


@dataclass(frozen=True)
class AffineBounds:
    absolute: int
    lower: int
    upper: int


@dataclass(frozen=True)
class DenseStackSummary:
    bucket: int
    architecture_offset: int
    fc0: tuple[AffineBounds, ...]
    fc1: tuple[AffineBounds, ...]
    fc2: AffineBounds
    fwd_lower: int
    fwd_upper: int


@dataclass(frozen=True)
class ParsedNetwork:
    description: bytes
    size: int
    sha256: str
    selected: Mapping[str, int]
    spans: Mapping[str, WireSpan]
    psqt_top32_sums: tuple[int, ...]
    dense_stacks: tuple[DenseStackSummary, ...]


class _Reader:
    def __init__(
        self,
        stream: BinaryIO,
        *,
        patches: Optional[Mapping[int, int]] = None,
        limit: Optional[int] = None,
    ) -> None:
        if limit is not None and limit < 0:
            raise ValueError("read limit must be non-negative")
        self.stream = stream
        self.offset = 0
        self.limit = limit
        self.digest = hashlib.sha256()
        self.patches = dict(patches or {})
        if any(offset < 0 or not 0 <= value <= 255 for offset, value in self.patches.items()):
            raise ValueError("patch offsets and byte values must be non-negative/u8")
        if limit is not None and any(offset >= limit for offset in self.patches):
            raise ValueError("corruption patch lies outside the read limit")

    def read_some(self, size: int) -> bytes:
        if size < 0:
            raise ValueError("negative read size")
        if self.limit is not None:
            size = min(size, max(0, self.limit - self.offset))
        start = self.offset
        data = self.stream.read(size)
        if data and self.patches:
            relevant = [
                (offset, value)
                for offset, value in self.patches.items()
                if start <= offset < start + len(data)
            ]
            if relevant:
                mutable = bytearray(data)
                for offset, value in relevant:
                    mutable[offset - start] = value
                data = bytes(mutable)
        self.offset += len(data)
        self.digest.update(data)
        return data

    def read_exact(self, size: int, context: str) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = self.read_some(remaining)
            if not chunk:
                raise WireError(f"truncated {context} at byte {self.offset}")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def u32(self, context: str) -> int:
        return struct.unpack("<I", self.read_exact(4, context))[0]

    def i32(self, context: str) -> int:
        return struct.unpack("<i", self.read_exact(4, context))[0]


def encode_sleb128(value: int) -> bytes:
    encoded = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        done = (value == 0 and not byte & 0x40) or (value == -1 and byte & 0x40)
        encoded.append(byte if done else byte | 0x80)
        if done:
            return bytes(encoded)


def _signed_single_byte(byte: int) -> int:
    return byte if byte < 0x40 else byte - 0x80


def _selection(
    selected: Optional[Mapping[str, Iterable[int]]]
) -> dict[str, tuple[int, ...]]:
    return {
        name: tuple(sorted(set(indices)))
        for name, indices in (selected or {}).items()
    }


def _record_selected(
    output: dict[str, int], name: str, index: int, value: int
) -> None:
    output[f"{name}[{index}]"] = value


def forward_interval(
    fc2: tuple[int, int],
    fc0_30: tuple[int, int],
    fc0_31: tuple[int, int],
) -> tuple[int, int]:
    """Return the exact signed interval for ``fc2 + fc0[30] - fc0[31]``."""

    return (
        fc2[0] + fc0_30[0] - fc0_31[1],
        fc2[1] + fc0_30[1] - fc0_31[0],
    )


def validate_forward_interval(
    fc2: tuple[int, int],
    fc0_30: tuple[int, int],
    fc0_31: tuple[int, int],
    *,
    context: str,
) -> tuple[int, int]:
    """Apply the frozen asymmetric raw-output envelope to three intervals."""

    lower, upper = forward_interval(fc2, fc0_30, fc0_31)
    if lower < FWD_MIN or upper > FWD_MAX:
        raise WireError(f"{context}: dense skip/output envelope exceeded")
    return lower, upper


def _read_sleb_tensor(
    reader: _Reader,
    name: str,
    elements: int,
    bits: int,
    selections: Mapping[str, tuple[int, ...]],
    selected_output: dict[str, int],
    spans: dict[str, WireSpan],
    on_nonzero: Optional[Callable[[int, int], None]] = None,
) -> None:
    frame_offset = reader.offset
    if reader.read_exact(len(LEB128_MAGIC), f"{name} magic") != LEB128_MAGIC:
        raise WireError(f"{name}: missing COMPRESSED_LEB128 marker")
    length_offset = reader.offset
    payload_size = reader.u32(f"{name} compressed byte count")
    payload_offset = reader.offset
    spans[name] = WireSpan(
        frame_offset, payload_offset, payload_size, elements, length_offset
    )

    wanted = selections.get(name, ())
    if wanted and (wanted[0] < 0 or wanted[-1] >= elements):
        raise ValueError(f"selected {name} index is outside the declared tensor")
    wanted_cursor = 0
    element_index = 0
    remaining = payload_size
    pending = bytearray()
    value = 0
    shift = 0
    minimum = -(1 << (bits - 1))
    maximum = (1 << (bits - 1)) - 1
    max_bytes = (bits + 6) // 7
    if payload_size < elements:
        raise WireError(f"{name}: compressed payload is shorter than its element count")
    if payload_size > elements * max_bytes:
        raise WireError(f"{name}: compressed payload exceeds its canonical maximum")

    while remaining:
        chunk = reader.read_exact(min(remaining, STREAM_CHUNK), f"{name} payload")
        remaining -= len(chunk)

        if not pending and chunk.isascii():
            start = element_index
            end = start + len(chunk)
            if end > elements:
                raise WireError(f"{name}: compressed payload has too many values")
            cursor = bisect_left(wanted, start, wanted_cursor)
            while cursor < len(wanted) and wanted[cursor] < end:
                index = wanted[cursor]
                scalar = _signed_single_byte(chunk[index - start])
                _record_selected(selected_output, name, index, scalar)
                cursor += 1
            wanted_cursor = cursor
            if on_nonzero is not None and chunk.count(0) != len(chunk):
                for relative, byte in enumerate(chunk):
                    if byte:
                        on_nonzero(start + relative, _signed_single_byte(byte))
            element_index = end
            continue

        for byte in chunk:
            pending.append(byte)
            value |= (byte & 0x7F) << shift
            shift += 7
            if len(pending) > max_bytes:
                raise WireError(f"{name}: overlong signed LEB128 value")
            if byte & 0x80:
                continue
            if byte & 0x40:
                value -= 1 << shift
            raw = bytes(pending)
            if not minimum <= value <= maximum:
                raise WireError(f"{name}: signed LEB128 value is outside i{bits}")
            if encode_sleb128(value) != raw:
                raise WireError(f"{name}: non-canonical signed LEB128 value")
            if element_index >= elements:
                raise WireError(f"{name}: compressed payload has too many values")
            if wanted_cursor < len(wanted) and wanted[wanted_cursor] == element_index:
                _record_selected(selected_output, name, element_index, value)
                wanted_cursor += 1
            if on_nonzero is not None and value:
                on_nonzero(element_index, value)
            element_index += 1
            pending.clear()
            value = 0
            shift = 0

    if pending:
        raise WireError(f"{name}: unterminated signed LEB128 value")
    if element_index != elements:
        raise WireError(
            f"{name}: decoded {element_index} values, expected {elements}"
        )
    if wanted_cursor != len(wanted):
        raise WireError(f"{name}: failed to observe every selected value")


def _read_raw_i8_tensor(
    reader: _Reader,
    name: str,
    elements: int,
    selections: Mapping[str, tuple[int, ...]],
    selected_output: dict[str, int],
    spans: dict[str, WireSpan],
) -> None:
    payload_offset = reader.offset
    spans[name] = WireSpan(payload_offset, payload_offset, elements, elements)
    wanted = selections.get(name, ())
    if wanted and (wanted[0] < 0 or wanted[-1] >= elements):
        raise ValueError(f"selected {name} index is outside the declared tensor")
    cursor = 0
    consumed = 0
    while consumed < elements:
        chunk = reader.read_exact(min(STREAM_CHUNK, elements - consumed), name)
        end = consumed + len(chunk)
        cursor = bisect_left(wanted, consumed, cursor)
        while cursor < len(wanted) and wanted[cursor] < end:
            index = wanted[cursor]
            byte = chunk[index - consumed]
            _record_selected(
                selected_output, name, index, byte if byte < 128 else byte - 256
            )
            cursor += 1
        consumed = end


def _read_i32_vector(
    reader: _Reader,
    name: str,
    count: int,
    selections: Mapping[str, tuple[int, ...]],
    selected_output: dict[str, int],
    spans: dict[str, WireSpan],
) -> tuple[int, ...]:
    offset = reader.offset
    raw = reader.read_exact(count * 4, name)
    spans[name] = WireSpan(offset, offset, len(raw), count)
    values = struct.unpack(f"<{count}i", raw)
    for index in selections.get(name, ()):
        if not 0 <= index < count:
            raise ValueError(f"selected {name} index is outside the declared vector")
        _record_selected(selected_output, name, index, values[index])
    return values


def _read_dense_weights(
    reader: _Reader,
    name: str,
    biases: tuple[int, ...],
    inputs: int,
    selections: Mapping[str, tuple[int, ...]],
    selected_output: dict[str, int],
    spans: dict[str, WireSpan],
) -> tuple[AffineBounds, ...]:
    offset = reader.offset
    spans[name] = WireSpan(offset, offset, len(biases) * inputs, len(biases) * inputs)
    wanted = selections.get(name, ())
    if wanted and (wanted[0] < 0 or wanted[-1] >= len(biases) * inputs):
        raise ValueError(f"selected {name} index is outside the declared tensor")
    wanted_cursor = 0
    bounds: list[AffineBounds] = []
    for output, bias in enumerate(biases):
        raw = reader.read_exact(inputs, f"{name} output {output}")
        values = memoryview(raw).cast("b")
        base = output * inputs
        while wanted_cursor < len(wanted) and wanted[wanted_cursor] < base + inputs:
            index = wanted[wanted_cursor]
            _record_selected(selected_output, name, index, int(values[index - base]))
            wanted_cursor += 1
        absolute = abs(bias) + 127 * sum(abs(value) for value in values)
        lower = bias + 127 * sum(value for value in values if value < 0)
        upper = bias + 127 * sum(value for value in values if value > 0)
        bounds.append(AffineBounds(absolute, lower, upper))
    return tuple(bounds)


def _parse_dense_stack(
    reader: _Reader,
    bucket: int,
    selections: Mapping[str, tuple[int, ...]],
    selected_output: dict[str, int],
    spans: dict[str, WireSpan],
) -> DenseStackSummary:
    prefix = f"dense.{bucket}"
    architecture_offset = reader.offset
    if reader.u32(f"{prefix} architecture hash") != ARCHITECTURE_HASH:
        raise WireError(f"{prefix}: architecture hash mismatch")

    fc0_biases = _read_i32_vector(
        reader,
        f"{prefix}.fc0_biases",
        FC0_OUTPUTS,
        selections,
        selected_output,
        spans,
    )
    fc0 = _read_dense_weights(
        reader,
        f"{prefix}.fc0_weights",
        fc0_biases,
        FC0_INPUTS,
        selections,
        selected_output,
        spans,
    )
    fc1_biases = _read_i32_vector(
        reader,
        f"{prefix}.fc1_biases",
        FC1_OUTPUTS,
        selections,
        selected_output,
        spans,
    )
    fc1 = _read_dense_weights(
        reader,
        f"{prefix}.fc1_weights",
        fc1_biases,
        FC1_INPUTS,
        selections,
        selected_output,
        spans,
    )
    fc2_biases = _read_i32_vector(
        reader,
        f"{prefix}.fc2_bias",
        1,
        selections,
        selected_output,
        spans,
    )
    fc2_tuple = _read_dense_weights(
        reader,
        f"{prefix}.fc2_weights",
        fc2_biases,
        FC2_INPUTS,
        selections,
        selected_output,
        spans,
    )
    fc2 = fc2_tuple[0]
    fwd_lower, fwd_upper = forward_interval(
        (fc2.lower, fc2.upper),
        (fc0[30].lower, fc0[30].upper),
        (fc0[31].lower, fc0[31].upper),
    )
    return DenseStackSummary(
        bucket, architecture_offset, fc0, fc1, fc2, fwd_lower, fwd_upper
    )


def _validate_numeric(
    psqt_magnitude_overflow: bool,
    psqt_sums: tuple[int, ...],
    dense: tuple[DenseStackSummary, ...],
) -> None:
    """Apply numeric gates in the same post-EOF precedence as the C++ reader."""

    if psqt_magnitude_overflow:
        raise WireError("hm_psqt: INT32_MIN/magnitude exceeds INT32_MAX")
    if any(total > INT32_MAX for total in psqt_sums):
        raise WireError("hm_psqt: top-32 active-weight envelope exceeded")

    for stack in dense:
        prefix = f"dense.{stack.bucket}"
        for layer, bounds in (("fc0", stack.fc0), ("fc1", stack.fc1)):
            for output, bound in enumerate(bounds):
                if bound.absolute > INT32_MAX:
                    raise WireError(
                        f"{prefix}.{layer}_weights output {output}: "
                        "affine i32 envelope exceeded"
                    )
        if stack.fc2.absolute > INT32_MAX:
            raise WireError(
                f"{prefix}.fc2_weights output 0: affine i32 envelope exceeded"
            )
        validate_forward_interval(
            (stack.fc2.lower, stack.fc2.upper),
            (stack.fc0[30].lower, stack.fc0[30].upper),
            (stack.fc0[31].lower, stack.fc0[31].upper),
            context=prefix,
        )


def assert_hash_contract() -> None:
    actual = (
        SLICE_HASHES,
        FEATURE_HASH,
        len(TRANSFORMER_DESCRIPTOR),
        TRANSFORMER_DESCRIPTOR_HASH,
        FEATURE_TRANSFORMER_HASH,
        NETWORK_HASH,
    )
    expected = (
        EXPECTED_SLICE_HASHES,
        EXPECTED_FEATURE_HASH,
        799,
        EXPECTED_TRANSFORMER_DESCRIPTOR_HASH,
        EXPECTED_FEATURE_TRANSFORMER_HASH,
        EXPECTED_NETWORK_HASH,
    )
    if actual != expected:
        raise AssertionError(f"AtomicNNUEV3 Python hash contract drifted: {actual!r}")


def parse_network(
    path: Path,
    *,
    selected_indices: Optional[Mapping[str, Iterable[int]]] = None,
    patches: Optional[Mapping[int, int]] = None,
    limit: Optional[int] = None,
) -> ParsedNetwork:
    """Parse one candidate transactionally using bounded memory.

    ``patches`` and ``limit`` are read-only corruption overlays used by tests;
    they avoid cloning the roughly 77 MiB fixture for every negative case.
    """

    assert_hash_contract()
    path = path.expanduser().resolve()
    if limit is not None and limit < 0:
        raise ValueError("read limit must be non-negative")
    physical_size = path.stat().st_size
    visible_size = physical_size if limit is None else min(physical_size, limit)
    if patches and any(offset < 0 or offset >= visible_size for offset in patches):
        raise ValueError("corruption patch lies outside the visible input")
    selections = _selection(selected_indices)
    selected_output: dict[str, int] = {}
    spans: dict[str, WireSpan] = {}
    psqt_heaps: list[list[int]] = [[] for _ in range(PSQT_BUCKETS)]
    psqt_magnitude_overflow = False

    def observe_psqt(index: int, value: int) -> None:
        nonlocal psqt_magnitude_overflow
        magnitude = abs(value)
        if magnitude > INT32_MAX:
            psqt_magnitude_overflow = True
            return
        heap = psqt_heaps[index % PSQT_BUCKETS]
        if len(heap) < 32:
            heapq.heappush(heap, magnitude)
        elif magnitude > heap[0]:
            heapq.heapreplace(heap, magnitude)

    with path.open("rb") as stream:
        reader = _Reader(stream, patches=patches, limit=limit)
        if reader.u32("file version") != FILE_VERSION:
            raise WireError("AtomicNNUEV3 file version mismatch")
        if reader.u32("network hash") != NETWORK_HASH:
            raise WireError("AtomicNNUEV3 network hash mismatch")
        description_size = reader.u32("description length")
        if description_size > MAX_DESCRIPTION_BYTES:
            raise WireError("description exceeds the fail-closed size limit")
        description = reader.read_exact(description_size, "description")
        if reader.u32("feature-transformer hash") != FEATURE_TRANSFORMER_HASH:
            raise WireError("AtomicNNUEV3 feature-transformer hash mismatch")

        _read_sleb_tensor(
            reader,
            "biases",
            ACCUMULATOR_DIMENSIONS,
            16,
            selections,
            selected_output,
            spans,
        )
        _read_sleb_tensor(
            reader,
            "hm",
            HM_DIMENSIONS * ACCUMULATOR_DIMENSIONS,
            16,
            selections,
            selected_output,
            spans,
        )
        _read_raw_i8_tensor(
            reader,
            "capture_pair",
            CAPTURE_PAIR_DIMENSIONS * ACCUMULATOR_DIMENSIONS,
            selections,
            selected_output,
            spans,
        )
        _read_sleb_tensor(
            reader,
            "king_blast_ep",
            KING_BLAST_EP_DIMENSIONS * ACCUMULATOR_DIMENSIONS,
            16,
            selections,
            selected_output,
            spans,
        )
        _read_raw_i8_tensor(
            reader,
            "blast_ring",
            BLAST_RING_DIMENSIONS * ACCUMULATOR_DIMENSIONS,
            selections,
            selected_output,
            spans,
        )
        _read_sleb_tensor(
            reader,
            "hm_psqt",
            HM_DIMENSIONS * PSQT_BUCKETS,
            32,
            selections,
            selected_output,
            spans,
            observe_psqt,
        )
        psqt_sums = tuple(sum(heap) for heap in psqt_heaps)

        dense = tuple(
            _parse_dense_stack(
                reader, bucket, selections, selected_output, spans
            )
            for bucket in range(LAYER_STACKS)
        )
        if reader.read_some(1):
            raise WireError("trailing bytes after the eighth dense stack")
        if any(offset >= reader.offset for offset in reader.patches):
            raise ValueError("a requested corruption patch lies beyond parsed input")
        size = reader.offset
        digest = reader.digest.hexdigest().upper()

    _validate_numeric(psqt_magnitude_overflow, psqt_sums, dense)

    expected_selected = sum(len(indices) for indices in selections.values())
    if len(selected_output) != expected_selected:
        missing = expected_selected - len(selected_output)
        raise WireError(f"{missing} requested selected values were not observed")
    return ParsedNetwork(
        description,
        size,
        digest,
        dict(selected_output),
        dict(spans),
        psqt_sums,
        dense,
    )


def roundtrip_network(source: Path, target: Path) -> ParsedNetwork:
    """Validate then copy one canonical network without overwriting a target."""

    source = source.expanduser().resolve()
    target = target.expanduser().resolve()
    parsed = parse_network(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with target.open("xb") as output:
            created = True
            with source.open("rb") as input_stream:
                digest = hashlib.sha256()
                copied = 0
                while True:
                    chunk = input_stream.read(STREAM_CHUNK)
                    if not chunk:
                        break
                    written = output.write(chunk)
                    if written != len(chunk):
                        raise OSError("short write while copying AtomicNNUEV3 network")
                    digest.update(chunk)
                    copied += len(chunk)
        copied_digest = digest.hexdigest().upper()
        if (
            copied != parsed.size
            or target.stat().st_size != parsed.size
            or copied_digest != parsed.sha256
        ):
            raise AssertionError(
                "AtomicNNUEV3 round-trip copy differs from the validated source"
            )
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise
    return parsed


assert_hash_contract()
