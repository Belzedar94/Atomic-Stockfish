from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest


TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import atomic_syzygy_uci as syzygy_uci


def test_multi_directory_path_is_resolved_in_declared_order(tmp_path):
    dirs = [tmp_path / "3-4-5", tmp_path / "6-wdl", tmp_path / "6-dtz"]
    for directory in dirs:
        directory.mkdir()

    normalized = syzygy_uci.normalize_table_dirs(dirs)
    assert normalized == tuple(directory.resolve() for directory in dirs)
    assert syzygy_uci.syzygy_path_value(dirs) == os.pathsep.join(
        str(directory.resolve()) for directory in dirs
    )


def test_multi_directory_path_rejects_duplicates(tmp_path):
    directory = tmp_path / "tables"
    directory.mkdir()
    with pytest.raises(AssertionError, match="duplicate"):
        syzygy_uci.normalize_table_dirs((directory, directory / "."))


def test_fixture_lookup_crosses_directories_and_rejects_ambiguity(tmp_path):
    wdl = tmp_path / "wdl"
    dtz = tmp_path / "dtz"
    wdl.mkdir()
    dtz.mkdir()
    expected = wdl / syzygy_uci.KPPPPVK_WDL
    expected.write_bytes(b"wdl")
    assert syzygy_uci.find_table_file((wdl, dtz), expected.name) == expected

    (dtz / expected.name).write_bytes(b"duplicate")
    with pytest.raises(AssertionError, match="ambiguous"):
        syzygy_uci.find_table_file((wdl, dtz), expected.name)


def test_six_man_limit_conformance_requires_limit6_tbhits():
    class FakeEngine:
        def __init__(self):
            self.limit = 6
            self.calls = []

        def setoption(self, name, value=None):
            self.calls.append((name, value))
            if name == "SyzygyProbeLimit":
                self.limit = int(value)

        def search(self, fen, go):
            assert fen == syzygy_uci.KPPPPVK_FEN
            assert go == "go depth 1"
            return syzygy_uci.SearchResult(
                output=[f"info depth 1 tbhits {1 if self.limit == 6 else 0}"],
                bestmove="a3a4",
                tb_hits=1 if self.limit == 6 else 0,
                score_kind="cp",
                score_value=1,
            )

    engine = FakeEngine()
    syzygy_uci.test_six_man_probe_limit(engine)
    assert ("SyzygyProbeLimit", "5") in engine.calls
    assert ("SyzygyProbeLimit", "6") in engine.calls


def test_six_man_limit_conformance_rejects_limit5_hits():
    class FakeEngine:
        def setoption(self, _name, _value=None):
            pass

        def search(self, _fen, _go):
            return syzygy_uci.SearchResult(
                output=["info depth 1 tbhits 1"],
                bestmove="a3a4",
                tb_hits=1,
                score_kind="cp",
                score_value=0,
            )

    with pytest.raises(AssertionError, match="SyzygyProbeLimit=5"):
        syzygy_uci.test_six_man_probe_limit(FakeEngine())


def test_kpppp_driver_output_freezes_wdl_dtz_and_root_contract():
    output = "\n".join(
        (
            "probe wdl=2 wdl_state=1 dtz=1 dtz_state=2",
            "root_no_rule50 ok=1 a3a4:1:1",
            "root_rule50 ok=1 a3a4:1:1",
            "root_wdl ok=1 a3a4:1:1",
            "rank_root root_in_tb=1 cardinality=0 use_rule50=1 probe_depth=1",
        )
    )
    syzygy_uci.validate_kpppp_driver_output(output)
    with pytest.raises(AssertionError, match="expected"):
        syzygy_uci.validate_kpppp_driver_output(output.replace("dtz=1", "dtz=2"))


def test_six_man_driver_isolates_the_required_pair(tmp_path, monkeypatch):
    wdl_dir = tmp_path / "wdl"
    dtz_dir = tmp_path / "dtz"
    wdl_dir.mkdir()
    dtz_dir.mkdir()
    (wdl_dir / syzygy_uci.KPPPPVK_WDL).write_bytes(b"wdl-fixture")
    (dtz_dir / syzygy_uci.KPPPPVK_DTZ).write_bytes(b"dtz-fixture")
    (wdl_dir / "unrelated.atbw").write_bytes(b"must-not-be-staged")

    output = "\n".join(
        (
            "probe wdl=2 wdl_state=1 dtz=1 dtz_state=2",
            "root_no_rule50 ok=1 a3a4:1:1",
            "root_rule50 ok=1 a3a4:1:1",
            "root_wdl ok=1 a3a4:1:1",
            "rank_root root_in_tb=1 cardinality=0 use_rule50=1 probe_depth=1",
        )
    )

    def fake_run(command, **_kwargs):
        isolated = Path(command[1])
        assert {path.name for path in isolated.iterdir()} == {
            syzygy_uci.KPPPPVK_WDL,
            syzygy_uci.KPPPPVK_DTZ,
        }
        assert (isolated / syzygy_uci.KPPPPVK_WDL).read_bytes() == b"wdl-fixture"
        assert (isolated / syzygy_uci.KPPPPVK_DTZ).read_bytes() == b"dtz-fixture"
        return syzygy_uci.subprocess.CompletedProcess(command, 0, output)

    monkeypatch.setattr(syzygy_uci.subprocess, "run", fake_run)
    syzygy_uci.test_six_man_driver(
        tmp_path / "driver.exe", (wdl_dir, dtz_dir), timeout=1.0
    )


def test_analysis_book_requires_hits_only_when_limit6(tmp_path):
    book = tmp_path / "endgames.epd"
    book.write_text(
        "8/Q7/p6K/8/1Q3k2/8/8/q7 b - - 0 1\n"
        "n7/8/8/8/8/8/8/1K2NkR1 b - - 0 1\n",
        encoding="utf-8",
    )

    class FakeEngine:
        def __init__(self):
            self.limit = 6
            self.searches = []

        def setoption(self, name, value=None):
            if name == "SyzygyProbeLimit":
                self.limit = int(value)

        def search(self, fen, go):
            self.searches.append((fen, go, self.limit))
            return syzygy_uci.SearchResult(
                output=[f"info depth 1 tbhits {1 if self.limit == 6 else 0}"],
                bestmove="f4g5",
                tb_hits=1 if self.limit == 6 else 0,
                score_kind="cp",
                score_value=0,
            )

    engine = FakeEngine()
    results = syzygy_uci.analyze_six_man_book(engine, book)
    assert len(results) == 2
    assert [limit for _fen, _go, limit in engine.searches] == [6, 0, 6, 0]
