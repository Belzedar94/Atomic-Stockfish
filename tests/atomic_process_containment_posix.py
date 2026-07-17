#!/usr/bin/env python3
"""Prove POSIX exact-tag containment fails before an adversarial target starts."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


def load_helper(repo_root: Path) -> Any:
    helper = (repo_root / "scripts" / "atomic_process_containment.py").resolve(
        strict=True
    )
    spec = importlib.util.spec_from_file_location(
        "_atomic_posix_containment_rejection", helper
    )
    if spec is None or spec.loader is None:
        raise AssertionError("could not load containment helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if Path(str(module.__file__)).resolve(strict=True) != helper:
        raise AssertionError("containment helper origin mismatch")
    return module


def main() -> int:
    if os.name != "posix":
        raise AssertionError("POSIX containment rejection ran on another platform")
    if os.environ.get("ATOMIC_EXPECT_NO_INIT_PID1") == "1" and os.getpid() != 1:
        raise AssertionError("no-init Docker regression controller is not PID 1")
    if len(sys.argv) != 2:
        raise AssertionError("expected the repository root argument")
    repo_root = Path(sys.argv[1]).resolve(strict=True)
    helper = load_helper(repo_root)

    with tempfile.TemporaryDirectory(prefix="atomic-posix-rejection-") as temporary:
        root = Path(temporary).resolve(strict=True)
        marker = root / "target-started.txt"
        sleeper_pid = root / "sleeper.pid"
        attack = root / "kill-supervisor.py"
        attack.write_text(
            """import os, pathlib, signal, subprocess, sys, time
pathlib.Path(sys.argv[1]).write_text('target ran', encoding='ascii')
sleeper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
pathlib.Path(sys.argv[2]).write_text(str(sleeper.pid), encoding='ascii')
os.kill(os.getppid(), signal.SIGKILL)
time.sleep(120)
""",
            encoding="ascii",
        )
        try:
            helper.launch_contained(
                [sys.executable, str(attack), str(marker), str(sleeper_pid)],
                cwd=root,
                environment={
                    "HOME": str(root),
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONHASHSEED": "0",
                },
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except helper.ProcessContainmentError as error:
            if "unsupported on POSIX; the target was not started" not in str(error):
                raise AssertionError(f"unexpected rejection: {error}") from error
        else:
            raise AssertionError("POSIX adversarial target was unexpectedly launched")
        if marker.exists() or sleeper_pid.exists():
            raise AssertionError("POSIX containment rejection occurred after target start")

    source = (repo_root / "scripts" / "atomic_process_containment.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "PR_SET_CHILD_SUBREAPER",
        "--linux-supervisor",
        "start_new_session=True",
        "os.killpg",
    ):
        if forbidden in source:
            raise AssertionError(f"unsafe POSIX containment primitive remains: {forbidden}")
    print("POSIX supervisor-kill payload rejected before target creation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
