import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MOVEGEN = (ROOT / "src" / "movegen.cpp").read_text(encoding="utf-8")


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def function_body(signature: str, next_signature: str) -> str:
    start = MOVEGEN.index(signature)
    end = MOVEGEN.index(next_signature, start)
    return MOVEGEN[start:end]


def test_captures_masks_occupied_targets_next_to_own_king_only():
    generate_all = compact(function_body("Move* generate_all", "// <CAPTURES>"))

    assert (
        "const Bitboard kingAttacks = Attacks::attacks_bb<KING>(ksq);" in generate_all
    )
    assert "Type == CAPTURES ? pos.pieces(~Us) & ~kingAttacks" in generate_all
    assert "Type == NON_EVASIONS ? ~pos.pieces(Us)" in generate_all
    assert "Type == NON_EVASIONS ? ~pos.pieces(Us) & ~kingAttacks" not in generate_all


def test_pawn_captures_share_the_mask_but_non_evasions_keep_all_enemies():
    pawn_moves = compact(
        function_body("Move* generate_pawn_moves", "template<Color Us, PieceType Pt>")
    )

    assert (
        "const Bitboard enemies = Type == EVASIONS ? pos.checkers() : "
        "Type == CAPTURES ? target : pos.pieces(Them);"
        in pawn_moves
    )
    assert (
        "if constexpr (Type == CAPTURES) "
        "if (kingAttacks & pos.ep_square()) return moveList;"
        in pawn_moves
    )
    assert "if (Type == EVASIONS && (target & (pos.ep_square() + Up)))" in pawn_moves


def test_legal_generation_still_uses_non_evasions_and_the_legal_oracle():
    legal = compact(MOVEGEN[MOVEGEN.index("Move* generate<LEGAL>") :])

    assert "moveList = generate<NON_EVASIONS>(pos, moveList);" in legal
    assert "if (!pos.legal(*cur))" in legal
