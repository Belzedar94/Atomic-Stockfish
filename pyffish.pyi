from typing import Any, Optional

FEN_EMPTY: int
FEN_INVALID_BOARD_GEOMETRY: int
FEN_INVALID_CASTLING_INFO: int
FEN_INVALID_CHAR: int
FEN_INVALID_CHECK_COUNT: int
FEN_INVALID_COUNTING_RULE: int
FEN_INVALID_EN_PASSANT_SQ: int
FEN_INVALID_HALF_MOVE_COUNTER: int
FEN_INVALID_MOVE_COUNTER: int
FEN_INVALID_NB_PARTS: int
FEN_INVALID_NUMBER_OF_KINGS: int
FEN_INVALID_POCKET_INFO: int
FEN_INVALID_PROMOTED_PIECE: int
FEN_INVALID_SIDE_TO_MOVE: int
FEN_OK: int
FEN_TOUCHING_KINGS: int
NOTATION_DEFAULT: int
NOTATION_LAN: int
NOTATION_SAN: int
VALUE_DRAW: int
VALUE_MATE: int

def version() -> tuple[int, int, int]: ...
def info() -> str: ...
def variants() -> list[str]: ...
def set_option(name: str, value: Any) -> None: ...
def start_fen(variant: str) -> str: ...
def two_boards(variant: str) -> bool: ...
def captures_to_hand(variant: str) -> bool: ...
def get_san(
    variant: str,
    fen: str,
    move: str,
    chess960: bool = False,
    notation: int = NOTATION_DEFAULT,
) -> str: ...
def get_san_moves(
    variant: str,
    fen: str,
    movelist: list[str],
    chess960: bool = False,
    notation: int = NOTATION_DEFAULT,
) -> list[str]: ...
def legal_moves(
    variant: str,
    fen: str,
    movelist: list[str],
    chess960: bool = False,
) -> list[str]: ...
def get_fen(
    variant: str,
    fen: str,
    movelist: list[str],
    chess960: bool = False,
    sfen: bool = False,
    show_promoted: bool = False,
    count_started: int = 0,
) -> str: ...
def gives_check(
    variant: str,
    fen: str,
    movelist: list[str],
    chess960: bool = False,
) -> bool: ...
def is_capture(
    variant: str,
    fen: str,
    movelist: list[str],
    move: str,
    chess960: bool = False,
) -> bool: ...
def game_result(
    variant: str,
    fen: str,
    movelist: list[str],
    chess960: bool = False,
) -> int: ...
def is_immediate_game_end(
    variant: str,
    fen: str,
    movelist: list[str],
    chess960: bool = False,
) -> tuple[bool, int]: ...
def is_optional_game_end(
    variant: str,
    fen: str,
    movelist: list[str],
    chess960: bool = False,
    count_started: int = 0,
) -> tuple[bool, int]: ...
def has_insufficient_material(
    variant: str,
    fen: str,
    movelist: list[str],
    chess960: bool = False,
) -> tuple[bool, bool]: ...
def validate_fen(fen: str, variant: str, chess960: bool = False) -> int: ...
def perft(
    variant: str,
    fen: str,
    depth: int,
    chess960: bool = False,
    movelist: Optional[list[str]] = None,
) -> int: ...
