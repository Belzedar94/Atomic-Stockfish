#!/usr/bin/env python3
"""Contract and real-table tests for the native Atomic Syzygy adapter.

The four canonical WDL/DTZ values come from shakmaty-syzygy's Atomic test
suite. The en-passant and promotion fixtures were cross-checked against the
Lichess ``/atomic`` tablebase endpoint. No network access is used by this test.

``atomic_syzygy_driver.cpp`` links the real Position and prober directly, so
decoder failures can be isolated from the production UCI/search wiring while
still testing genuine ``.atbw/.atbz`` files.

Examples::

    python tests/atomic_syzygy.py --contract-only
    python tests/atomic_syzygy.py --driver ../research/atomic-syzygy-driver.exe \
        --tables ../research/shakmaty/shakmaty-syzygy/tables/atomic
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
PROBER_SOURCE = REPO_ROOT / "src" / "syzygy" / "tbprobe.cpp"

ATOMIC_WDL_MAGIC = bytes((0x55, 0x8D, 0xA4, 0x49))
ATOMIC_DTZ_MAGIC = bytes((0x91, 0xA9, 0x5E, 0xEB))

# Immutable fixture files published at
# https://tablebase.lichess.ovh/tables/atomic/3-4-5/.
FIXTURE_SHA256 = {
    "KPvK.atbw": "7a6c4ca84df5e34716d9fa6d0329d95352dac42b300fc5f0b02e3c8da0e3b584",
    "KPvK.atbz": "b6f0fdacd7dfd0c7c4501cee78c1b8984e3b642f7d89967691d79376f059dd37",
    "KPvKP.atbw": "0dace85b9cfdb9484250b75dbe8965c1b4cd88feff0a29170b5afdd485cffbfe",
    "KPvKP.atbz": "c49e253040c7ec8f21db57a1d63754158d47ce659ff80480b90e1045643f96f5",
    "KBBBvK.atbw": "114f101f74ab1469d749777b5b7e8b2ada5f47d31627ff60031f4832e6bf76a8",
    "KBBBvK.atbz": "f731d407f3ad8a0368d7f29762d0a70e407ee791dc0f5dcb88fc94eba987e31f",
    "KNNvK.atbw": "e4dc9886b296a1e2bf20670bec9f200be439221c686afd2a85097f8065b3bb24",
    "KQvK.atbw": "fdb2fb361b377aff5ce2f2610f244c006a8a12a28d805f6032e36fb28f18537d",
    "KRvK.atbw": "a17ff195ef2738f00f180e3dd8eb8bcd1d21e57642e78ff8f7b7ebffd233cceb",
    "KBvK.atbw": "fab2777a31956b845ca0a404dfaf03fc4c18275537670a36dbb28ab75d16d00f",
    "KNvK.atbw": "b71eaa7a3931f7b13fe4c0230a4bbe9b79ccdb21300ccb78e43314adf67c4929",
}

PROBE_RE = re.compile(
    r"^probe wdl=(-?\d+) wdl_state=(-?\d+) dtz=(-?\d+) dtz_state=(-?\d+)$",
    re.MULTILINE,
)
ROOT_RE = re.compile(r"^(root_(?:no_rule50|rule50|wdl)) ok=(\d)(.*)$", re.MULTILINE)
RANK_RE = re.compile(
    r"^rank_root root_in_tb=(\d) cardinality=(\d+) use_rule50=(\d) probe_depth=(-?\d+)$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class RootValue:
    rank: int
    score: int


@dataclass(frozen=True)
class DriverResult:
    wdl: int
    wdl_state: int
    dtz: int
    dtz_state: int
    roots_ok: dict[str, bool]
    roots: dict[str, dict[str, RootValue]]
    root_in_tb: bool
    cardinality: int
    use_rule50: bool
    probe_depth: int
    output: str


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_source_contract() -> None:
    source = PROBER_SOURCE.read_text(encoding="utf-8")

    require(
        re.search(
            r"AtomicWdlMagic\s*=\s*\{0x55,\s*0x8D,\s*0xA4,\s*0x49\}", source
        )
        is not None,
        "Atomic WDL magic must be named and equal 55 8D A4 49",
    )
    require(
        re.search(
            r"AtomicDtzMagic\s*=\s*\{0x91,\s*0xA9,\s*0x5E,\s*0xEB\}", source
        )
        is not None,
        "Atomic DTZ magic must be named and equal 91 A9 5E EB",
    )
    require('AtomicWdlSuffix = ".atbw"' in source, "missing .atbw suffix")
    require('AtomicDtzSuffix = ".atbz"' in source, "missing .atbz suffix")
    require("constexpr int TBPIECES = 6;" in source, "Atomic decoder must be capped at six")
    require("e.hasUniquePieces ? 31332 : 518" in source, "group pivot must be 518")
    require("MapA1D1D4[squares[0]] * 63" in source, "connected-king first branch missing")
    require("6 * 63 + rank_of(squares[0]) * 28" in source, "connected diagonal branch missing")
    require("6 * 63 + 4 * 28" in source, "connected double-diagonal branch missing")
    require(re.search(r"\bVariant\b", source) is None, "fixed Atomic prober must not add Variant")

    terminal = source.index("if (pos.is_atomic_terminal())")
    material_lookup = source.index("TBTables.get<Type>", terminal)
    require(terminal < material_lookup, "missing-king terminal must precede material lookup")

    search_start = source.index("template<bool CheckZeroingMoves>")
    search_end = source.index("}  // namespace", search_start)
    capture_search = source[search_start:search_end]
    require("MoveList<LEGAL>(pos)" in capture_search, "capture presearch lost legal move list")
    require("pos.capture(move)" in capture_search, "capture presearch lost capture handling")
    require(
        capture_search.index("value = -search<false>")
        < capture_search.index("value = probe_table<WDL>"),
        "captures, including en passant, must be searched before the stored WDL value",
    )
    require(
        "!pos.can_castle(ANY_CASTLING)" in source,
        "rank_root_moves must reject positions carrying castling rights",
    )

    print("PASS source contract: Atomic suffixes/magics/max6/terminal/EP/castling")


def _file(square: int) -> int:
    return square & 7


def _rank(square: int) -> int:
    return square >> 3


def _off_diagonal(square: int) -> int:
    return _rank(square) - _file(square)


def _canonical_pair(first: int, second: int) -> tuple[int, int]:
    squares = [first, second]
    if _file(squares[0]) > 3:
        squares = [square ^ 7 for square in squares]
    if _rank(squares[0]) > 3:
        squares = [square ^ 56 for square in squares]

    for index in range(2):
        if _off_diagonal(squares[index]) == 0:
            continue
        if _off_diagonal(squares[index]) > 0:
            for tail in range(index, 2):
                square = squares[tail]
                squares[tail] = ((square >> 3) | (square << 3)) & 63
        break
    return squares[0], squares[1]


def test_connected_king_domain() -> None:
    map_b1_h1_h7 = [0] * 64
    code = 0
    for square in range(64):
        if _off_diagonal(square) < 0:
            map_b1_h1_h7[square] = code
            code += 1
    require(code == 28, "B1-H1-H7 map must contain 28 squares")

    map_a1_d1_d4 = [0] * 64
    diagonal: list[int] = []
    code = 0
    for square in range(28):  # A1 through D4, inclusive.
        if _off_diagonal(square) < 0 and _file(square) <= 3:
            map_a1_d1_d4[square] = code
            code += 1
        elif _off_diagonal(square) == 0 and _file(square) <= 3:
            diagonal.append(square)
    for square in diagonal:
        map_a1_d1_d4[square] = code
        code += 1
    require(code == 10, "A1-D1-D4 map must contain 10 squares")

    indexes: set[int] = set()
    for first in range(64):
        for second in range(64):
            if first == second:
                continue
            first_c, second_c = _canonical_pair(first, second)
            adjust = int(second_c > first_c)

            if _off_diagonal(first_c):
                index = map_a1_d1_d4[first_c] * 63 + second_c - adjust
            elif _off_diagonal(second_c):
                index = 6 * 63 + _rank(first_c) * 28 + map_b1_h1_h7[second_c]
            else:
                index = (
                    6 * 63
                    + 4 * 28
                    + _rank(first_c) * 7
                    + _rank(second_c)
                    - adjust
                )
            indexes.add(index)

    require(indexes == set(range(518)), "connected king formula must cover exactly 0..517")
    print("PASS connected kings: exact contiguous 518-index domain (not orthodox 462)")


def validate_table_fixtures(tables: Path) -> None:
    missing = [name for name in FIXTURE_SHA256 if not (tables / name).is_file()]
    require(not missing, f"missing Atomic Syzygy fixtures in {tables}: {', '.join(missing)}")

    for name, expected_hash in FIXTURE_SHA256.items():
        data = (tables / name).read_bytes()
        expected_magic = ATOMIC_WDL_MAGIC if name.endswith(".atbw") else ATOMIC_DTZ_MAGIC
        require(data[:4] == expected_magic, f"{name}: unexpected format magic {data[:4].hex()}")
        actual_hash = hashlib.sha256(data).hexdigest()
        require(actual_hash == expected_hash, f"{name}: SHA-256 mismatch: {actual_hash}")

    print(f"PASS real table fixtures: {len(FIXTURE_SHA256)} headers and SHA-256 hashes")


def parse_driver_output(output: str) -> DriverResult:
    probe = PROBE_RE.search(output)
    require(probe is not None, f"driver emitted no probe record:\n{output}")

    roots_ok: dict[str, bool] = {}
    roots: dict[str, dict[str, RootValue]] = {}
    for match in ROOT_RE.finditer(output):
        label, ok, values = match.groups()
        roots_ok[label] = bool(int(ok))
        parsed: dict[str, RootValue] = {}
        for item in values.split():
            move, rank, score = item.rsplit(":", maxsplit=2)
            parsed[move] = RootValue(int(rank), int(score))
        roots[label] = parsed

    require(len(roots) == 3, f"driver emitted incomplete root records:\n{output}")
    rank = RANK_RE.search(output)
    require(rank is not None, f"driver emitted no rank_root record:\n{output}")

    return DriverResult(
        wdl=int(probe.group(1)),
        wdl_state=int(probe.group(2)),
        dtz=int(probe.group(3)),
        dtz_state=int(probe.group(4)),
        roots_ok=roots_ok,
        roots=roots,
        root_in_tb=bool(int(rank.group(1))),
        cardinality=int(rank.group(2)),
        use_rule50=bool(int(rank.group(3))),
        probe_depth=int(rank.group(4)),
        output=output,
    )


def run_driver(driver: Path, tables: Path, fen: str) -> DriverResult:
    completed = subprocess.run(
        [str(driver), str(tables), fen],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    require(
        completed.returncode == 0,
        f"driver exited {completed.returncode} for {fen}:\n{completed.stdout}",
    )
    return parse_driver_output(completed.stdout)


def assert_probe(
    result: DriverResult,
    *,
    wdl: int,
    dtz: int,
    wdl_state: int = 1,
    dtz_state: int = 1,
) -> None:
    actual = (result.wdl, result.dtz, result.wdl_state, result.dtz_state)
    expected = (wdl, dtz, wdl_state, dtz_state)
    require(actual == expected, f"expected probe {expected}, got {actual}:\n{result.output}")


def test_real_probes(driver: Path, tables: Path) -> None:
    # Interior probe and both root ranking paths.
    kbbb = run_driver(driver, tables, "5BBB/8/8/8/8/8/6k1/7K w - - 0 1")
    assert_probe(kbbb, wdl=2, dtz=33)
    require(kbbb.roots_ok["root_no_rule50"], "DTZ root probe failed for KBBBvK")
    require(kbbb.roots_ok["root_wdl"], "WDL root probe failed for KBBBvK")
    require(kbbb.root_in_tb, "rank_root_moves did not accept KBBBvK")

    # Adjacent kings force the 518-state pivot. The authoritative result is draw.
    connected = run_driver(driver, tables, "8/8/8/8/8/8/NkN5/1K6 w - - 0 1")
    assert_probe(connected, wdl=0, dtz=0)
    require(connected.root_in_tb, "adjacent-king KNNvK root probe failed")

    # Missing kings are terminal records, not material-table lookups.
    stm_exploded = run_driver(driver, tables, "8/8/8/8/8/8/2K5/8 b - - 0 1")
    assert_probe(stm_exploded, wdl=-2, dtz=-1)
    opponent_exploded = run_driver(driver, tables, "8/8/8/8/8/8/2k5/8 b - - 0 1")
    assert_probe(opponent_exploded, wdl=2, dtz=1)

    # En passant must be searched before consulting the stored WDL value.
    ep = run_driver(driver, tables, "7k/8/8/3pP3/8/8/8/K7 w - d6 0 1")
    assert_probe(ep, wdl=2, dtz=1, dtz_state=2)
    require(ep.roots["root_no_rule50"]["e5d6"] == RootValue(0, 0), "EP draw misranked")
    require(ep.roots["root_no_rule50"]["e5e6"].rank > 0, "winning pawn push misranked")

    # Promotion is another zeroing move whose children live in four material tables.
    promotion = run_driver(driver, tables, "k7/6P1/8/8/8/8/8/K7 w - - 0 1")
    assert_probe(promotion, wdl=2, dtz=1, dtz_state=2)
    require(promotion.roots["root_no_rule50"]["g7g8q"].rank > 0, "queen promotion lost")
    for move in ("g7g8r", "g7g8b", "g7g8n"):
        require(
            promotion.roots["root_no_rule50"][move] == RootValue(0, 0),
            f"drawing underpromotion {move} misranked",
        )

    # With 99 reversible plies, rule50-aware root probing adjudicates every
    # non-zeroing bishop move as an immediate draw; ignoring rule50 preserves wins.
    rule50 = run_driver(driver, tables, "5BBB/8/8/8/8/8/6k1/7K w - - 99 1")
    assert_probe(rule50, wdl=2, dtz=33)
    require(
        any(value.rank > 0 for value in rule50.roots["root_no_rule50"].values()),
        "rule50-disabled root probe should retain winning ranks",
    )
    require(
        all(value == RootValue(0, 0) for value in rule50.roots["root_rule50"].values()),
        "rule50-enabled root probe should adjudicate immediate draws",
    )

    # Syzygy tables carry no castling state. rank_root_moves must refuse them
    # even if an otherwise matching material file happens to be installed.
    castling = run_driver(driver, tables, "4k2r/8/8/8/8/8/8/R3K3 w Qk - 0 1")
    require(not castling.root_in_tb, "position with castling rights entered the tablebase")

    print("PASS real probes: interior/root/connected/terminal/EP/promotion/rule50/castling")


def test_swapped_magics(driver: Path, tables: Path) -> None:
    # This pawnless non-draw necessarily maps both files without first needing
    # promotion-child tables during the zeroing-move presearch.
    fen = "5BBB/8/8/8/8/8/6k1/7K w - - 0 1"
    with tempfile.TemporaryDirectory(prefix="atomic-syzygy-magic-") as temporary:
        destination = Path(temporary)
        for name in ("KBBBvK.atbw", "KBBBvK.atbz"):
            shutil.copy2(tables / name, destination / name)

        wdl = bytearray((destination / "KBBBvK.atbw").read_bytes())
        wdl[:4] = ATOMIC_DTZ_MAGIC
        (destination / "KBBBvK.atbw").write_bytes(wdl)
        swapped_wdl = run_driver(driver, destination, fen)
        require(swapped_wdl.wdl_state == 0, "DTZ magic was incorrectly accepted as WDL")
        require("Corrupted table" in swapped_wdl.output, "swapped WDL magic was not diagnosed")

        # Restore WDL and independently swap DTZ, catching the historical inverse bug.
        shutil.copy2(tables / "KBBBvK.atbw", destination / "KBBBvK.atbw")
        dtz = bytearray((destination / "KBBBvK.atbz").read_bytes())
        dtz[:4] = ATOMIC_WDL_MAGIC
        (destination / "KBBBvK.atbz").write_bytes(dtz)
        swapped_dtz = run_driver(driver, destination, fen)
        require(swapped_dtz.wdl_state == 1, "valid WDL failed beside swapped DTZ")
        require(swapped_dtz.dtz_state == 0, "WDL magic was incorrectly accepted as DTZ")
        require("Corrupted table" in swapped_dtz.output, "swapped DTZ magic was not diagnosed")

    print("PASS swapped magic: WDL/DTZ markers cannot be interchanged")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--contract-only",
        action="store_true",
        help="run source and 518-domain tests without the external table fixtures",
    )
    parser.add_argument(
        "--driver",
        type=Path,
        default=WORKSPACE_ROOT / "research" / "atomic-syzygy-driver.exe",
    )
    parser.add_argument(
        "--tables",
        type=Path,
        default=(
            WORKSPACE_ROOT
            / "research"
            / "shakmaty"
            / "shakmaty-syzygy"
            / "tables"
            / "atomic"
        ),
    )
    args = parser.parse_args()

    test_source_contract()
    test_connected_king_domain()

    if args.contract_only:
        print("Atomic Syzygy contract tests passed: 2/2")
        return 0

    driver = args.driver.resolve()
    tables = args.tables.resolve()
    if not driver.is_file():
        parser.error(
            f"integration driver does not exist: {driver}; build tests/atomic_syzygy_driver.cpp"
        )
    if not tables.is_dir():
        parser.error(f"Atomic table directory does not exist: {tables}")

    validate_table_fixtures(tables)
    test_real_probes(driver, tables)
    test_swapped_magics(driver, tables)
    print("Atomic Syzygy tests passed: 5/5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
