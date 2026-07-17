#!/usr/bin/env python3
"""Launch one command inside a fail-closed, killable process container.

Windows children start suspended, are assigned to a nested Job Object carrying
``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``, and are resumed only after assignment
succeeds.  Linux commands run below a dedicated ``PR_SET_CHILD_SUBREAPER``
supervisor.  That supervisor adopts, kills and reaps descendants even when they
double-fork or create a new session.  Other POSIX systems fail closed because a
process group alone cannot provide the same containment guarantee.
"""

from __future__ import annotations

import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence


class ProcessContainmentError(RuntimeError):
    """A child could not be contained or its complete tree did not terminate."""


_IS_LINUX = sys.platform.startswith("linux")
_LINUX_SUPERVISOR_READY_TIMEOUT_SECONDS = 15.0
_LINUX_MAX_DESCENDANTS = 65_536
_LINUX_WNOHANG = int(getattr(os, "WNOHANG", 1))
_LINUX_SIGSTOP = int(getattr(signal, "SIGSTOP", 19))
_LINUX_SIGKILL = int(getattr(signal, "SIGKILL", 9))
_LINUX_SIGHUP = int(getattr(signal, "SIGHUP", 1))


if _IS_LINUX:  # pragma: no branch - definitions are platform-specific
    import ctypes as _linux_ctypes

    _linux_libc = _linux_ctypes.CDLL(None, use_errno=True)
    _linux_libc.prctl.argtypes = [
        _linux_ctypes.c_int,
        _linux_ctypes.c_ulong,
        _linux_ctypes.c_ulong,
        _linux_ctypes.c_ulong,
        _linux_ctypes.c_ulong,
    ]
    _linux_libc.prctl.restype = _linux_ctypes.c_int

    _PR_SET_PDEATHSIG = 1
    _PR_SET_CHILD_SUBREAPER = 36


def _linux_prctl(option: int, argument: int, context: str) -> None:
    if not _IS_LINUX:  # pragma: no cover - guarded by the Linux launcher
        raise ProcessContainmentError(f"{context}: Linux is required")
    if _linux_libc.prctl(option, argument, 0, 0, 0) != 0:
        error_number = _linux_ctypes.get_errno()
        error = OSError(error_number, os.strerror(error_number))
        raise ProcessContainmentError(f"{context}: {error}")


def _linux_direct_children(pid: int) -> tuple[int, ...]:
    """Return kernel-recorded children, including adopted zombies."""

    path = Path(f"/proc/{pid}/task/{pid}/children")
    try:
        payload = path.read_text(encoding="ascii")
    except FileNotFoundError:
        return ()
    except OSError as error:
        raise ProcessContainmentError(
            f"could not inspect Linux child list for PID {pid}: {error}"
        ) from error
    children: list[int] = []
    for field in payload.split():
        if not field.isascii() or not field.isdecimal():
            raise ProcessContainmentError("Linux child list contains invalid data")
        child = int(field)
        if child <= 0:
            raise ProcessContainmentError("Linux child list contains an invalid PID")
        children.append(child)
    if len(children) != len(set(children)):
        raise ProcessContainmentError("Linux child list contains duplicate PIDs")
    return tuple(children)


def _linux_descendants(root_pid: int) -> tuple[int, ...]:
    pending = list(_linux_direct_children(root_pid))
    descendants: list[int] = []
    seen: set[int] = set()
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        descendants.append(pid)
        if len(descendants) > _LINUX_MAX_DESCENDANTS:
            raise ProcessContainmentError("Linux containment descendant bound exceeded")
        pending.extend(_linux_direct_children(pid))
    return tuple(descendants)


def _linux_signal_descendants(root_pid: int, signum: int) -> None:
    # Re-enumerate immediately before signalling so a recycled PID that is no
    # longer below the supervisor can never be targeted.
    for pid in _linux_descendants(root_pid):
        try:
            os.kill(pid, signum)
        except ProcessLookupError:
            pass
        except OSError as error:
            raise ProcessContainmentError(
                f"could not signal contained Linux PID {pid}: {error}"
            ) from error


def _linux_reap_available(
    target_pid: int | None, target_status: int | None
) -> int | None:
    while True:
        try:
            pid, status = os.waitpid(-1, _LINUX_WNOHANG)
        except ChildProcessError:
            return target_status
        except InterruptedError:
            continue
        except OSError as error:
            raise ProcessContainmentError(
                f"could not reap a contained Linux child: {error}"
            ) from error
        if pid == 0:
            return target_status
        if target_pid is not None and pid == target_pid:
            target_status = status


def _linux_drain_descendants(
    supervisor_pid: int, target_pid: int | None, target_status: int | None
) -> int | None:
    """Freeze, kill and reap until the subreaper has no descendants."""

    empty_observations = 0
    while True:
        target_status = _linux_reap_available(target_pid, target_status)
        descendants = _linux_descendants(supervisor_pid)
        if not descendants:
            empty_observations += 1
            if empty_observations >= 2:
                return target_status
            time.sleep(0.01)
            continue
        empty_observations = 0

        # Stopping every currently reachable descendant closes the fork race.
        # A second kernel walk catches children created just before their
        # parent observed SIGSTOP; SIGKILL then collapses every branch back to
        # this subreaper, where waitpid() can collect zombie-only trees.
        _linux_signal_descendants(supervisor_pid, _LINUX_SIGSTOP)
        time.sleep(0.005)
        _linux_signal_descendants(supervisor_pid, _LINUX_SIGKILL)
        target_status = _linux_reap_available(target_pid, target_status)
        time.sleep(0.005)


def _linux_status_exit_code(status: int) -> int:
    code = os.waitstatus_to_exitcode(status)
    if code < 0:
        return min(255, 128 + abs(code))
    return min(255, code)


def _write_linux_readiness(fd: int, payload: bytes) -> None:
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("readiness pipe accepted no bytes")
            view = view[written:]
    finally:
        os.close(fd)


def _linux_supervisor_main(readiness_fd: int, command: Sequence[str]) -> int:
    """Run inside the dedicated Linux subreaper process."""

    terminate_requested = False
    target: subprocess.Popen[bytes] | None = None
    target_status: int | None = None
    ready = False

    def request_termination(signum: int, frame: Any) -> None:
        del signum, frame
        nonlocal terminate_requested
        terminate_requested = True

    for signum in (signal.SIGTERM, signal.SIGINT, _LINUX_SIGHUP):
        signal.signal(signum, request_termination)

    try:
        parent_pid = os.getppid()
        _linux_prctl(
            _PR_SET_PDEATHSIG,
            signal.SIGTERM,
            "could not arm Linux supervisor parent-death signal",
        )
        _linux_prctl(
            _PR_SET_CHILD_SUBREAPER,
            1,
            "could not make Linux containment supervisor a subreaper",
        )
        if os.getppid() != parent_pid:
            raise ProcessContainmentError(
                "Linux containment parent exited during supervisor setup"
            )
        # Prove procfs child accounting is available before untrusted code runs.
        _linux_direct_children(os.getpid())
        if terminate_requested:
            raise ProcessContainmentError(
                "Linux containment terminated before target launch"
            )
        target = subprocess.Popen(
            list(command),
            env=dict(os.environ),
            shell=False,
            close_fds=True,
        )
        _write_linux_readiness(readiness_fd, f"READY {target.pid}\n".encode("ascii"))
        ready = True

        while target_status is None and not terminate_requested:
            target_status = _linux_reap_available(target.pid, target_status)
            if target_status is None:
                time.sleep(0.01)

        target_status = _linux_drain_descendants(
            os.getpid(), target.pid, target_status
        )
        if target_status is None:
            raise ProcessContainmentError("Linux target exited without wait status")
        target.returncode = os.waitstatus_to_exitcode(target_status)
        if terminate_requested:
            return 128 + signal.SIGTERM
        return _linux_status_exit_code(target_status)
    except BaseException as error:
        if not ready:
            try:
                _write_linux_readiness(
                    readiness_fd,
                    ("ERROR " + str(error).replace("\r", " ").replace("\n", " "))[
                        :2048
                    ].encode("utf-8", "replace")
                    + b"\n",
                )
            except OSError:
                pass
        if target is not None:
            try:
                target_status = _linux_drain_descendants(
                    os.getpid(), target.pid, target_status
                )
                if target_status is not None:
                    target.returncode = os.waitstatus_to_exitcode(target_status)
            except BaseException as cleanup_error:
                print(
                    f"Linux containment cleanup failure: {cleanup_error}",
                    file=sys.stderr,
                    flush=True,
                )
        print(f"Linux containment failure: {error}", file=sys.stderr, flush=True)
        return 125


def _read_linux_supervisor_readiness(
    fd: int, process: subprocess.Popen[bytes]
) -> int:
    deadline = time.monotonic() + _LINUX_SUPERVISOR_READY_TIMEOUT_SECONDS
    payload = bytearray()
    try:
        while b"\n" not in payload and len(payload) <= 4096:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProcessContainmentError(
                    "Linux containment supervisor readiness timed out"
                )
            try:
                readable, _, _ = select.select([fd], [], [], remaining)
            except InterruptedError:
                continue
            if not readable:
                raise ProcessContainmentError(
                    "Linux containment supervisor readiness timed out"
                )
            chunk = os.read(fd, 4096 - len(payload) + 1)
            if not chunk:
                break
            payload.extend(chunk)
    finally:
        os.close(fd)
    if len(payload) > 4096 or payload.count(b"\n") != 1 or not payload.endswith(b"\n"):
        raise ProcessContainmentError(
            "Linux containment supervisor emitted invalid readiness data"
        )
    line = bytes(payload[:-1])
    prefix = b"READY "
    if line.startswith(prefix) and line[len(prefix) :].isdigit():
        pid = int(line[len(prefix) :])
        if pid > 0:
            return pid
    detail = line.decode("utf-8", "replace")
    if process.poll() is not None:
        detail += f" (supervisor exited {process.returncode})"
    raise ProcessContainmentError(
        f"Linux containment supervisor did not become ready: {detail}"
    )


def _launch_linux_supervisor(
    command: Sequence[str],
    *,
    cwd: os.PathLike[str] | str,
    environment: Mapping[str, str],
    stdin: Any,
    stdout: Any,
    stderr: Any,
) -> "ContainedProcess":
    read_fd, write_fd = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    try:
        helper = Path(__file__).resolve(strict=True)
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-S",
                str(helper),
                "--linux-supervisor",
                str(write_fd),
                "--",
                *command,
            ],
            cwd=cwd,
            env=dict(environment),
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            shell=False,
            start_new_session=True,
            pass_fds=(write_fd,),
        )
        os.close(write_fd)
        write_fd = -1
        try:
            target_pid = _read_linux_supervisor_readiness(read_fd, process)
        finally:
            read_fd = -1
        return ContainedProcess(process, ("linux-subreaper", target_pid))
    except BaseException as error:
        if read_fd >= 0:
            os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)
        cleanup_error: BaseException | None = None
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=15)
            except BaseException as candidate:
                cleanup_error = candidate
        if cleanup_error is not None:
            raise ProcessContainmentError(
                f"failed Linux supervisor launch was not contained: {cleanup_error}"
            ) from error
        if isinstance(error, ProcessContainmentError):
            raise
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise ProcessContainmentError(
            f"could not create Linux containment supervisor: {error}"
        ) from error


def _launch_posix_contained(
    command: Sequence[str],
    *,
    cwd: os.PathLike[str] | str,
    environment: Mapping[str, str],
    stdin: Any,
    stdout: Any,
    stderr: Any,
) -> "ContainedProcess":
    if not _IS_LINUX:
        raise ProcessContainmentError(
            "fail-closed process-tree containment is available only on Windows and Linux"
        )
    return _launch_linux_supervisor(
        command,
        cwd=cwd,
        environment=environment,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
    )


if os.name == "nt":  # pragma: no branch - definitions are platform-specific
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _CREATE_SUSPENDED = 0x00000004
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
    _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS = 1
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100
    _THREAD_SUSPEND_RESUME = 0x0002
    _TH32CS_SNAPTHREAD = 0x00000004
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _ERROR_NO_MORE_FILES = 18
    _RESUME_FAILED = 0xFFFFFFFF

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class _THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Thread32First.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_THREADENTRY32),
    ]
    _kernel32.Thread32First.restype = wintypes.BOOL
    _kernel32.Thread32Next.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_THREADENTRY32),
    ]
    _kernel32.Thread32Next.restype = wintypes.BOOL
    _kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenThread.restype = wintypes.HANDLE
    _kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


def _windows_error(context: str) -> ProcessContainmentError:
    if os.name != "nt":  # pragma: no cover - only called by Windows helpers
        return ProcessContainmentError(context)
    error = ctypes.WinError(ctypes.get_last_error())
    return ProcessContainmentError(f"{context}: {error}")


def _close_windows_handle(handle: Any, context: str) -> None:
    if not _kernel32.CloseHandle(handle):
        raise _windows_error(f"could not close {context}")


def _create_windows_job() -> Any:
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        raise _windows_error("could not create Windows Job Object")
    try:
        limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise _windows_error("could not set kill-on-close Job Object limit")
    except BaseException:
        try:
            _close_windows_handle(job, "failed Job Object")
        except ProcessContainmentError:
            pass
        raise
    return job


def _assign_process_to_windows_job(job: Any, pid: int) -> None:
    access = _PROCESS_SET_QUOTA | _PROCESS_TERMINATE
    process_handle = _kernel32.OpenProcess(access, False, pid)
    if not process_handle:
        raise _windows_error("could not open suspended child for Job assignment")
    try:
        if not _kernel32.AssignProcessToJobObject(job, process_handle):
            raise _windows_error(
                "could not assign suspended child to nested Job Object"
            )
    finally:
        _close_windows_handle(process_handle, "Job-assignment process handle")


def _primary_thread_id(pid: int) -> int:
    snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if not snapshot or snapshot == _INVALID_HANDLE_VALUE:
        raise _windows_error("could not enumerate suspended child threads")
    try:
        entry = _THREADENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        if not _kernel32.Thread32First(snapshot, ctypes.byref(entry)):
            raise _windows_error("could not read the suspended child thread snapshot")
        matches: list[int] = []
        while True:
            if int(entry.th32OwnerProcessID) == pid:
                matches.append(int(entry.th32ThreadID))
            entry.dwSize = ctypes.sizeof(entry)
            if not _kernel32.Thread32Next(snapshot, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error != _ERROR_NO_MORE_FILES:
                    raise _windows_error("could not finish suspended thread enumeration")
                break
        if len(matches) != 1:
            raise ProcessContainmentError(
                f"suspended child has {len(matches)} primary-thread candidates"
            )
        return matches[0]
    finally:
        _close_windows_handle(snapshot, "thread snapshot")


def _resume_windows_process(pid: int) -> None:
    thread_id = _primary_thread_id(pid)
    thread = _kernel32.OpenThread(_THREAD_SUSPEND_RESUME, False, thread_id)
    if not thread:
        raise _windows_error("could not open suspended primary thread")
    try:
        previous = int(_kernel32.ResumeThread(thread))
        if previous == _RESUME_FAILED:
            raise _windows_error("could not resume suspended primary thread")
        if previous != 1:
            raise ProcessContainmentError(
                f"suspended primary thread had unexpected suspend count {previous}"
            )
    finally:
        _close_windows_handle(thread, "primary thread")


def _windows_active_processes(job: Any) -> int:
    accounting = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    returned = wintypes.DWORD()
    if not _kernel32.QueryInformationJobObject(
        job,
        _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS,
        ctypes.byref(accounting),
        ctypes.sizeof(accounting),
        ctypes.byref(returned),
    ):
        raise _windows_error("could not query Job Object accounting")
    return int(accounting.ActiveProcesses)


def _terminate_windows_job(job: Any, deadline: float, errors: list[str]) -> None:
    if not _kernel32.TerminateJobObject(job, 1):
        errors.append(str(_windows_error("could not terminate Job Object")))
        return
    while True:
        try:
            active = _windows_active_processes(job)
        except ProcessContainmentError as error:
            errors.append(str(error))
            return
        if active == 0:
            return
        if time.monotonic() >= deadline:
            errors.append(f"Job Object still contains {active} active processes")
            return
        time.sleep(0.01)


def _close_process_streams(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


class ContainedProcess:
    """A ``Popen`` plus its retained process-tree containment token."""

    def __init__(self, process: subprocess.Popen[bytes], token: Any) -> None:
        self.process = process
        self._token = token
        self._closed = False

    def terminate_tree(self, timeout: float = 15.0) -> None:
        """Terminate and verify the complete contained tree exactly once."""

        if self._closed:
            return
        deadline = time.monotonic() + timeout
        errors: list[str] = []
        if os.name == "nt":
            job = self._token
            try:
                _terminate_windows_job(job, deadline, errors)
            finally:
                try:
                    _close_windows_handle(job, "Job Object")
                except ProcessContainmentError as error:
                    errors.append(str(error))
        elif _IS_LINUX:
            try:
                if self.process.poll() is None:
                    os.kill(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError as error:
                errors.append(f"could not request Linux supervisor cleanup: {error}")
        else:  # pragma: no cover - launch_contained rejects this platform
            errors.append("unsupported POSIX containment platform")

        self._closed = True
        remaining = max(0.0, deadline - time.monotonic())
        try:
            self.process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            if _IS_LINUX:
                # Do not kill the subreaper and thereby release adopted
                # descendants.  It remains alive, continuously draining its
                # tree, while the release gate fails closed.
                errors.append("Linux containment supervisor did not finish cleanup")
            else:
                errors.append("contained root process did not terminate")
                try:
                    self.process.kill()
                    self.process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        except OSError as error:
            errors.append(f"could not reap contained root process: {error}")
        if errors:
            raise ProcessContainmentError("; ".join(errors))

    def __del__(self) -> None:  # pragma: no cover - emergency interpreter cleanup
        if self._closed:
            return
        if os.name == "nt":
            try:
                _kernel32.CloseHandle(self._token)
            except BaseException:
                pass
        elif _IS_LINUX:
            try:
                os.kill(self.process.pid, signal.SIGTERM)
            except BaseException:
                pass
        self._closed = True


def launch_contained(
    command: Sequence[str],
    *,
    cwd: os.PathLike[str] | str,
    environment: Mapping[str, str],
    stdin: Any,
    stdout: Any,
    stderr: Any,
) -> ContainedProcess:
    """Create a contained child, or reap it before returning an error."""

    if os.name == "nt":
        job = _create_windows_job()
        process: subprocess.Popen[bytes] | None = None
        assigned = False
        try:
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | _CREATE_SUSPENDED
            process = subprocess.Popen(
                list(command),
                cwd=cwd,
                env=dict(environment),
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                shell=False,
                creationflags=flags,
            )
            _assign_process_to_windows_job(job, process.pid)
            assigned = True
            _resume_windows_process(process.pid)
            return ContainedProcess(process, job)
        except BaseException as error:
            cleanup_errors: list[str] = []
            deadline = time.monotonic() + 15.0
            try:
                if process is not None:
                    if assigned:
                        _terminate_windows_job(job, deadline, cleanup_errors)
                    else:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        except OSError as cleanup_error:
                            cleanup_errors.append(
                                f"could not kill unassigned suspended child: {cleanup_error}"
                            )
            finally:
                try:
                    _close_windows_handle(job, "failed Job Object")
                except ProcessContainmentError as cleanup_error:
                    cleanup_errors.append(str(cleanup_error))
            if process is not None:
                try:
                    process.wait(timeout=max(0.0, deadline - time.monotonic()))
                except (OSError, subprocess.TimeoutExpired) as cleanup_error:
                    cleanup_errors.append(
                        f"could not reap failed suspended child: {cleanup_error}"
                    )
                _close_process_streams(process)
            if cleanup_errors:
                raise ProcessContainmentError(
                    "failed Windows launch was not cleanly contained: "
                    + "; ".join(cleanup_errors)
                ) from error
            if isinstance(error, ProcessContainmentError):
                raise
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise ProcessContainmentError(
                f"could not create contained Windows child: {error}"
            ) from error

    return _launch_posix_contained(
        command,
        cwd=cwd,
        environment=environment,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
    )


def _main(arguments: Sequence[str]) -> int:
    if (
        len(arguments) < 4
        or arguments[0] != "--linux-supervisor"
        or arguments[2] != "--"
        or not arguments[1].isascii()
        or not arguments[1].isdecimal()
    ):
        print("atomic_process_containment.py is an internal helper", file=sys.stderr)
        return 2
    readiness_fd = int(arguments[1])
    if readiness_fd < 3 or not arguments[3:]:
        print("invalid Linux containment supervisor invocation", file=sys.stderr)
        return 2
    if not _IS_LINUX:
        print("Linux containment supervisor invoked on another platform", file=sys.stderr)
        return 125
    return _linux_supervisor_main(readiness_fd, arguments[3:])


if __name__ == "__main__":  # pragma: no cover - exercised in a real subprocess
    raise SystemExit(_main(sys.argv[1:]))
