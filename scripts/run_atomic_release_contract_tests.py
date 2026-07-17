#!/usr/bin/env python3
"""Run every pure-Python Atomic release-contract regression.

Release-contract tests follow one of three reviewed naming conventions.  Keeping
discovery here gives the normal Atomic CI and the tag-only release workflow one
authoritative gate, so adding a matching regression cannot silently leave it
orphaned in one of the workflows.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Optional, Sequence

import pytest


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / "tests" / "python"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PATTERNS = (
    "test_*release*.py",
    "test_atomic_reproducible_*.py",
    "test_atomic_syzygy_openbench_evidence.py",
    "test_atomic_windows_wheel_fingerprint.py",
    "test_wasm_source_date_epoch.py",
)


def discover_release_contract_tests() -> tuple[Path, ...]:
    tests = {path.resolve() for pattern in PATTERNS for path in TEST_ROOT.glob(pattern)}
    if not tests:
        raise RuntimeError("no Atomic release-contract tests were discovered")
    return tuple(sorted(tests))


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = ["-q", *(str(path) for path in discover_release_contract_tests())]
    if argv:
        arguments.extend(argv)
    return int(pytest.main(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
