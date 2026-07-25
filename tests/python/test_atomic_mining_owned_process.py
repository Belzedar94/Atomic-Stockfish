from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.atomic_mining import owned_process
from tools.atomic_mining.owned_process import OwnedProcess, OwnedProcessError


@pytest.mark.skipif(os.name != "nt", reason="production containment is Windows")
def test_natural_exit_proves_job_active_zero(tmp_path: Path) -> None:
    stdin = tmp_path / "stdin.bin"
    stdin.write_bytes(b"")
    stdout = tmp_path / "stdout.bin"
    stderr = tmp_path / "stderr.bin"
    with (
        stdin.open("rb") as stdin_stream,
        stdout.open("wb") as stdout_stream,
        stderr.open("wb") as stderr_stream,
    ):
        owned = OwnedProcess.launch(
            [sys.executable, "-c", "raise SystemExit(0)"],
            stdin=stdin_stream,
            stdout=stdout_stream,
            stderr=stderr_stream,
            cwd=str(tmp_path),
            env=dict(os.environ),
        )
        owned.process.wait(timeout=10.0)
        owned.prove_natural_zero(timeout=10.0)
        evidence = owned.evidence()
        assert evidence.containment == "windows-job-object"
        assert evidence.created_suspended_before_assignment is True
        assert evidence.resumed_primary_thread is True
        assert evidence.active_processes_zero is True
        assert evidence.termination_requested is False
        owned.close()
    assert stdout.read_bytes() == b""
    assert stderr.read_bytes() == b""


@pytest.mark.skipif(os.name != "nt", reason="production containment is Windows")
def test_forced_cleanup_kills_wrapper_and_descendant(tmp_path: Path) -> None:
    script = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        "time.sleep(60)"
    )
    stdin = tmp_path / "stdin.bin"
    stdin.write_bytes(b"")
    stdout = tmp_path / "stdout.bin"
    stderr = tmp_path / "stderr.bin"
    with (
        stdin.open("rb") as stdin_stream,
        stdout.open("wb") as stdout_stream,
        stderr.open("wb") as stderr_stream,
    ):
        owned = OwnedProcess.launch(
            [sys.executable, "-c", script],
            stdin=stdin_stream,
            stdout=stdout_stream,
            stderr=stderr_stream,
            cwd=str(tmp_path),
            env=dict(os.environ),
        )
        with pytest.raises(subprocess.TimeoutExpired):
            owned.process.wait(timeout=0.1)
        owned.terminate_all(timeout=10.0)
        evidence = owned.evidence()
        assert evidence.active_processes_zero is True
        assert evidence.termination_requested is True
        assert owned.process.returncode == 70
        owned.close()


@pytest.mark.skipif(os.name != "nt", reason="production containment is Windows")
def test_assignment_failure_kills_unassigned_suspended_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[object] = []
    real_create = owned_process._create_windows_process

    def recording_create(*args: object, **kwargs: object) -> tuple[object, object]:
        process, thread = real_create(*args, **kwargs)
        captured.append(process)
        return process, thread

    monkeypatch.setattr(owned_process, "_create_windows_process", recording_create)
    monkeypatch.setattr(
        owned_process, "_assign_process_to_job", lambda *_args: False
    )
    stdout = tmp_path / "stdout.bin"
    stderr = tmp_path / "stderr.bin"
    stdin = tmp_path / "stdin.bin"
    stdin.write_bytes(b"")
    with (
        stdin.open("rb") as stdin_stream,
        stdout.open("wb") as stdout_stream,
        stderr.open("wb") as stderr_stream,
    ):
        with pytest.raises(
            OwnedProcessError, match="AssignProcessToJobObject"
        ):
            OwnedProcess.launch(
                [sys.executable, "-c", "import time;time.sleep(60)"],
                stdin=stdin_stream,
                stdout=stdout_stream,
                stderr=stderr_stream,
                cwd=str(tmp_path),
                env=dict(os.environ),
            )
    assert len(captured) == 1
    assert captured[0].returncode == 70


@pytest.mark.skipif(os.name != "nt", reason="production containment is Windows")
def test_device_stdio_is_rejected_before_process_creation(tmp_path: Path) -> None:
    stdout = tmp_path / "stdout.bin"
    stderr = tmp_path / "stderr.bin"
    with (
        open(os.devnull, "rb") as stdin_stream,
        stdout.open("wb") as stdout_stream,
        stderr.open("wb") as stderr_stream,
    ):
        with pytest.raises(OwnedProcessError, match="regular file"):
            OwnedProcess.launch(
                [sys.executable, "-c", "raise SystemExit(0)"],
                stdin=stdin_stream,
                stdout=stdout_stream,
                stderr=stderr_stream,
                cwd=str(tmp_path),
                env=dict(os.environ),
            )


@pytest.mark.skipif(os.name != "nt", reason="production containment is Windows")
def test_resume_count_must_be_exactly_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_resume = owned_process._kernel32.ResumeThread
    monkeypatch.setattr(
        owned_process._kernel32,
        "ResumeThread",
        lambda thread: (real_resume(thread) and 2),
    )
    stdin = tmp_path / "stdin.bin"
    stdin.write_bytes(b"")
    stdout = tmp_path / "stdout.bin"
    stderr = tmp_path / "stderr.bin"
    with (
        stdin.open("rb") as stdin_stream,
        stdout.open("wb") as stdout_stream,
        stderr.open("wb") as stderr_stream,
    ):
        with pytest.raises(OwnedProcessError, match="exactly one"):
            OwnedProcess.launch(
                [sys.executable, "-c", "raise SystemExit(0)"],
                stdin=stdin_stream,
                stdout=stdout_stream,
                stderr=stderr_stream,
                cwd=str(tmp_path),
                env=dict(os.environ),
            )
