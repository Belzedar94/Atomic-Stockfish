#!/usr/bin/env python3
"""Real Linux no-init regression for exact-tag process containment."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any


def load_helper(repo_root: Path) -> Any:
    helper = (repo_root / "scripts" / "atomic_process_containment.py").resolve(
        strict=True
    )
    spec = importlib.util.spec_from_file_location(
        "_atomic_linux_containment_regression", helper
    )
    if spec is None or spec.loader is None:
        raise AssertionError("could not load containment helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if Path(str(module.__file__)).resolve(strict=True) != helper:
        raise AssertionError("containment helper origin mismatch")
    return module


def process_identity(pid: int) -> str | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").split()
    except FileNotFoundError:
        return None
    if len(fields) < 22:
        raise AssertionError(f"invalid /proc identity for PID {pid}")
    return fields[21]


def wait_identity_gone(pid: int, identity: str, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = process_identity(pid)
        if current is None or current != identity:
            return True
        time.sleep(0.02)
    return False


def run_contained(helper: Any, root: Path, script: Path, record: Path) -> bytes:
    contained = helper.launch_contained(
        [sys.executable, str(script), str(record)],
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
    try:
        output, errors = contained.process.communicate(timeout=15)
    finally:
        contained.terminate_tree(timeout=10)
    if contained.process.returncode != 0:
        raise AssertionError(
            f"contained regression exited {contained.process.returncode}: {errors!r}"
        )
    if errors:
        raise AssertionError(f"contained regression emitted stderr: {errors!r}")
    return output


def setsid_double_fork_case(helper: Any, root: Path) -> None:
    record = root / "setsid.json"
    script = root / "setsid-double-fork.py"
    script.write_text(
        """import json, os, pathlib, sys, time
record = pathlib.Path(sys.argv[1])
child = os.fork()
if child == 0:
    os.setsid()
    detached = os.fork()
    if detached == 0:
        record.write_text(json.dumps({
            'pid': os.getpid(), 'ppid': os.getppid(), 'sid': os.getsid(0),
            'identity': pathlib.Path(f'/proc/{os.getpid()}/stat').read_text().split()[21],
        }), encoding='ascii')
        print('detached setsid descendant ready', flush=True)
        time.sleep(120)
        os._exit(0)
    os._exit(0)
os.waitpid(child, 0)
deadline = time.monotonic() + 5
while not record.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
if not record.exists():
    raise SystemExit('detached descendant did not publish identity')
print('double-fork root exiting', flush=True)
""",
        encoding="ascii",
    )
    output = run_contained(helper, root, script, record)
    if b"double-fork root exiting" not in output:
        raise AssertionError("setsid root output was not captured")
    value = json.loads(record.read_text(encoding="ascii"))
    getsid = getattr(os, "getsid")
    if value["sid"] == getsid(0):
        raise AssertionError("fixture did not escape into a distinct session")
    if not wait_identity_gone(value["pid"], value["identity"]):
        raise AssertionError(f"setsid descendant {value['pid']} survived containment")


def setsid_timeout_case(helper: Any, root: Path) -> None:
    record = root / "setsid-timeout.json"
    script = root / "setsid-timeout.py"
    script.write_text(
        """import json, os, pathlib, sys, time
record = pathlib.Path(sys.argv[1])
child = os.fork()
if child == 0:
    os.setsid()
    detached = os.fork()
    if detached == 0:
        record.write_text(json.dumps({
            'pid': os.getpid(),
            'identity': pathlib.Path(f'/proc/{os.getpid()}/stat').read_text().split()[21],
        }), encoding='ascii')
        time.sleep(120)
        os._exit(0)
    os._exit(0)
os.waitpid(child, 0)
deadline = time.monotonic() + 5
while not record.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
if not record.exists():
    raise SystemExit('detached timeout descendant did not publish identity')
print('setsid timeout tree ready', flush=True)
time.sleep(120)
""",
        encoding="ascii",
    )
    contained = helper.launch_contained(
        [sys.executable, str(script), str(record)],
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
    try:
        deadline = time.monotonic() + 5
        while not record.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not record.exists():
            raise AssertionError("setsid timeout fixture did not become ready")
        value = json.loads(record.read_text(encoding="ascii"))
        contained.terminate_tree(timeout=10)
        output, errors = contained.process.communicate(timeout=2)
    finally:
        if not contained._closed:
            contained.terminate_tree(timeout=10)
    if contained.process.returncode != 128 + 15:
        raise AssertionError(
            f"terminated supervisor returned {contained.process.returncode}: {errors!r}"
        )
    if b"setsid timeout tree ready" not in output or errors:
        raise AssertionError("setsid timeout fixture output mismatch")
    if not wait_identity_gone(value["pid"], value["identity"]):
        raise AssertionError(
            f"timed-out setsid descendant {value['pid']} survived containment"
        )


def zombie_reaping_case(helper: Any, root: Path) -> None:
    record = root / "zombie.json"
    script = root / "zombie-parent.py"
    script.write_text(
        """import json, os, pathlib, sys, time
record = pathlib.Path(sys.argv[1])
child = os.fork()
if child == 0:
    os._exit(0)
identity = pathlib.Path(f'/proc/{child}/stat').read_text().split()[21]
deadline = time.monotonic() + 5
state = ''
while time.monotonic() < deadline:
    state = pathlib.Path(f'/proc/{child}/stat').read_text().split()[2]
    if state == 'Z':
        break
    time.sleep(0.01)
if state != 'Z':
    raise SystemExit('fixture child never became a zombie')
record.write_text(json.dumps({'pid': child, 'identity': identity}), encoding='ascii')
print('zombie observed before root exit', flush=True)
""",
        encoding="ascii",
    )
    output = run_contained(helper, root, script, record)
    if b"zombie observed before root exit" not in output:
        raise AssertionError("zombie fixture output was not captured")
    value = json.loads(record.read_text(encoding="ascii"))
    if not wait_identity_gone(value["pid"], value["identity"]):
        raise AssertionError(f"adopted zombie {value['pid']} was not reaped")


def main() -> int:
    if not sys.platform.startswith("linux"):
        raise AssertionError("Linux containment regression ran on another platform")
    if os.environ.get("ATOMIC_EXPECT_NO_INIT_PID1") == "1" and os.getpid() != 1:
        raise AssertionError("no-init Docker regression controller is not PID 1")
    if len(sys.argv) != 2:
        raise AssertionError("expected the repository root argument")
    helper = load_helper(Path(sys.argv[1]))
    with tempfile.TemporaryDirectory(prefix="atomic-containment-") as temporary:
        root = Path(temporary).resolve(strict=True)
        setsid_double_fork_case(helper, root)
        setsid_timeout_case(helper, root)
        zombie_reaping_case(helper, root)
    print("Linux setsid, timeout, double-fork and zombie containment passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
