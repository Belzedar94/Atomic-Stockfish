import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
BUILDER_PATH = ROOT / "tests" / "wasm-engine" / "build.py"
SPEC = importlib.util.spec_from_file_location("atomic_wasm_builder", BUILDER_PATH)
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def test_wasm_build_surfaces_preserve_a_frozen_epoch_and_default_to_zero() -> None:
    makefile = (ROOT / "src" / "Makefile_js").read_text(encoding="utf-8")
    powershell = (ROOT / "tests" / "wasm-engine" / "build.ps1").read_text(
        encoding="utf-8"
    )

    assert "SOURCE_DATE_EPOCH ?= 0" in makefile
    assert "export SOURCE_DATE_EPOCH" in makefile
    assert "SOURCE_DATE_EPOCH=0 $(CXX)" not in makefile
    assert "$env:SOURCE_DATE_EPOCH = '0'" in powershell
    assert "$null -eq $env:SOURCE_DATE_EPOCH" in powershell
    assert (
        "$env:SOURCE_DATE_EPOCH -notmatch '\\A(?:0|[1-9][0-9]*)\\z'"
        in powershell
    )
    assert BUILDER.normalized_source_date_epoch({}) == "0"
    assert (
        BUILDER.normalized_source_date_epoch({"SOURCE_DATE_EPOCH": "1777777777"})
        == "1777777777"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "-1",
        "+1",
        "01",
        "1.0",
        "epoch",
        " 1",
        "1 ",
        "1\n",
        "1\r",
        "1\r\n",
        "\u0661",
        "\uff11",
    ],
)
def test_wasm_builder_rejects_noncanonical_epochs(value: str) -> None:
    with pytest.raises(ValueError, match="canonical positive decimal"):
        BUILDER.normalized_source_date_epoch({"SOURCE_DATE_EPOCH": value})


def test_complete_wasm_builder_passes_and_records_the_frozen_epoch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "stub.cpp"
    source.write_text("// stub\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    calls: list[tuple[list[str], dict[str, str]]] = []

    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1777777777")
    monkeypatch.setattr(BUILDER, "parse_sources", lambda: [source])

    def fake_run(command, *, check, env, capture_output=False, text=False):
        assert check is True
        calls.append((list(command), dict(env)))
        if "--version" in command:
            return subprocess.CompletedProcess(
                command, 0, stdout="em++ fake 1.0\n", stderr=""
            )
        output = Path(command[command.index("-o") + 1])
        output.write_text("javascript\n", encoding="utf-8")
        (output.parent / "atomic-stockfish-nnue.wasm").write_bytes(b"wasm")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(BUILDER.subprocess, "run", fake_run)
    BUILDER.build(out_dir, "em++", False)

    assert len(calls) == 2
    assert all(env["SOURCE_DATE_EPOCH"] == "1777777777" for _, env in calls)
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sourceDateEpoch"] == 1777777777
