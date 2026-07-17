#!/usr/bin/env python3
"""Launch one command inside a fail-closed, killable process container.

Windows children start suspended, are assigned to a nested Job Object carrying
``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``, and are resumed only after assignment
succeeds.  The retained Job handle is closed only after the complete process
tree is terminated and verified.  POSIX is rejected before target creation:
same-UID descendants can kill any userspace supervisor, so a process group or
subreaper cannot meet this release gate's fail-closed contract.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any, Mapping, Sequence


class ProcessContainmentError(RuntimeError):
    """A child could not be contained or its complete tree did not terminate."""


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
        if os.name != "nt":  # pragma: no cover - POSIX launch is rejected
            raise ProcessContainmentError("unsupported containment platform")
        job = self._token
        try:
            _terminate_windows_job(job, deadline, errors)
        finally:
            try:
                _close_windows_handle(job, "Job Object")
            except ProcessContainmentError as error:
                errors.append(str(error))

        self._closed = True
        remaining = max(0.0, deadline - time.monotonic())
        try:
            self.process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
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
        if self._closed or os.name != "nt":
            return
        try:
            _kernel32.CloseHandle(self._token)
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

    raise ProcessContainmentError(
        "exact-tag process containment is unsupported on POSIX; "
        "the target was not started"
    )
