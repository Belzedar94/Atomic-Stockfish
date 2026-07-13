from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
OPENBENCH = MAKEFILE.split("### Section 12. OpenBench build shim", 1)[1]


def test_openbench_only_replaces_the_default_goal_when_evalfile_is_present():
    assert re.search(
        r"ifneq \(\$\(strip \$\(EVALFILE\)\),\)\s*"
        r"\.DEFAULT_GOAL := openbench\s*endif\s*$",
        OPENBENCH,
    )


def test_openbench_selects_the_normative_compiler_on_windows_and_linux():
    mapping = re.search(
        r"ifeq \(\$\(OS\),Windows_NT\)\s*"
        r"OPENBENCH_COMP = (?P<windows>\S+)\s*else\s*"
        r"OPENBENCH_COMP = (?P<linux>\S+)\s*endif",
        OPENBENCH,
    )
    assert mapping is not None
    assert mapping.groupdict() == {"windows": "mingw", "linux": "gcc"}


def test_openbench_forwards_worker_output_and_compiler_to_the_bmi2_build():
    assert re.search(
        r"\+\$\(MAKE\) build EXE=\"\$\(EXE\)\" CXX=\"\$\(CXX\)\"\s*\\\s*"
        r"ARCH=x86-64-bmi2 COMP=\$\(OPENBENCH_COMP\)",
        OPENBENCH,
    )


def test_openbench_embeds_the_authenticated_network_under_the_canonical_name():
    assert "OPENBENCH_NET = atomic_run3b_e202_l05.nnue" in OPENBENCH
    assert 'cp "$(EVALFILE)" "$(OPENBENCH_NET).tmp"' in OPENBENCH
    assert 'mv "$(OPENBENCH_NET).tmp" "$(OPENBENCH_NET)"' in OPENBENCH
    assert "-DATOMIC_NNUE_EMBEDDING" in OPENBENCH
