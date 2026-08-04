"""Race-free ownership of result-bearing E00 subprocess trees.

Windows production uses the documented sequence required by the E00 trust
contract:

``CreateProcessW(CREATE_SUSPENDED) -> AssignProcessToJobObject ->
ResumeThread(primary thread)``.

The private Job Object has ``KILL_ON_JOB_CLOSE`` enabled.  A wrapper exit is
never sufficient evidence of cleanup: ``ActiveProcesses`` must reach zero.
Standard streams are regular files, not pipes, so there are no hidden pipe
reader threads whose incomplete shutdown could be mistaken for completion.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import signal
import stat
import subprocess
import time
from typing import Any, BinaryIO, Mapping, Sequence


class OwnedProcessError(RuntimeError):
    """Creation, cleanup, or zero-descendant proof failed closed."""


@dataclass(frozen=True)
class OwnedProcessEvidence:
    schema: str
    containment: str
    kill_on_close: bool
    created_suspended_before_assignment: bool
    resumed_primary_thread: bool
    active_processes_zero: bool
    termination_requested: bool

    def payload(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "containment": self.containment,
            "kill_on_close": self.kill_on_close,
            "created_suspended_before_assignment": (
                self.created_suspended_before_assignment
            ),
            "resumed_primary_thread": self.resumed_primary_thread,
            "active_processes_zero": self.active_processes_zero,
            "termination_requested": self.termination_requested,
        }


if os.name == "nt":  # pragma: no branch - production path
    import ctypes
    from ctypes import wintypes
    import msvcrt

    _CREATE_SUSPENDED = 0x00000004
    _CREATE_NEW_PROCESS_GROUP = 0x00000200
    _CREATE_UNICODE_ENVIRONMENT = 0x00000400
    _STARTF_USESTDHANDLES = 0x00000100
    _DUPLICATE_SAME_ACCESS = 0x00000002
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
    _WAIT_OBJECT_0 = 0x00000000
    _WAIT_TIMEOUT = 0x00000102
    _WAIT_FAILED = 0xFFFFFFFF
    _STILL_ACTIVE = 259
    _INFINITE = 0xFFFFFFFF

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

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

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateProcessW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOW),
        ctypes.POINTER(_PROCESS_INFORMATION),
    )
    _kernel32.CreateProcessW.restype = wintypes.BOOL
    _kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = (
        wintypes.HANDLE,
        wintypes.HANDLE,
    )
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
    )
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.GetExitCodeProcess.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _kernel32.DuplicateHandle.argtypes = (
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    _kernel32.DuplicateHandle.restype = wintypes.BOOL
    _kernel32.GetCurrentProcess.argtypes = ()
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL


def _positive_timeout(value: float, *, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value <= 0
        or value != value
        or value in {float("inf"), float("-inf")}
    ):
        raise ValueError(f"{label} must be finite and positive")
    return float(value)


if os.name == "nt":  # pragma: no branch - production path

    def _close_handle(handle: Any, *, label: str) -> None:
        if handle and not _kernel32.CloseHandle(handle):
            raise OwnedProcessError(
                f"CloseHandle({label}) failed with {ctypes.get_last_error()}"
            )


    def _duplicate_inheritable_stream(stream: BinaryIO, *, label: str) -> Any:
        try:
            descriptor = stream.fileno()
            status = os.fstat(descriptor)
            if not stat.S_ISREG(status.st_mode):
                raise OwnedProcessError(
                    f"{label} must be backed by a regular file"
                )
            raw = msvcrt.get_osfhandle(descriptor)
        except (AttributeError, OSError, ValueError) as error:
            raise OwnedProcessError(
                f"{label} must be an open regular-file stream"
            ) from error
        duplicate = wintypes.HANDLE()
        current = _kernel32.GetCurrentProcess()
        if not _kernel32.DuplicateHandle(
            current,
            wintypes.HANDLE(raw),
            current,
            ctypes.byref(duplicate),
            0,
            True,
            _DUPLICATE_SAME_ACCESS,
        ):
            raise OwnedProcessError(
                f"DuplicateHandle({label}) failed with {ctypes.get_last_error()}"
            )
        return duplicate


    def _environment_block(env: Mapping[str, str] | None) -> Any | None:
        if env is None:
            return None
        rendered: list[str] = []
        for key, value in sorted(env.items(), key=lambda item: item[0].upper()):
            if (
                not isinstance(key, str)
                or not key
                or "=" in key
                or "\x00" in key
                or not isinstance(value, str)
                or "\x00" in value
            ):
                raise OwnedProcessError("child environment is malformed")
            rendered.append(f"{key}={value}")
        return ctypes.create_unicode_buffer("\x00".join(rendered) + "\x00\x00")


    class _WindowsCreatedProcess:
        """Minimal Popen-compatible owner for a CreateProcessW handle."""

        def __init__(self, handle: Any, pid: int) -> None:
            self._handle = handle
            self.pid = pid
            self.returncode: int | None = None
            self._closed = False

        def poll(self) -> int | None:
            if self.returncode is not None:
                return self.returncode
            code = wintypes.DWORD()
            if not _kernel32.GetExitCodeProcess(
                self._handle, ctypes.byref(code)
            ):
                raise OwnedProcessError(
                    "GetExitCodeProcess failed with "
                    f"{ctypes.get_last_error()}"
                )
            if code.value == _STILL_ACTIVE:
                return None
            self.returncode = int(code.value)
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            if timeout is None:
                milliseconds = _INFINITE
            else:
                seconds = _positive_timeout(timeout, label="process wait timeout")
                milliseconds = max(1, min(int(seconds * 1000), 0xFFFFFFFE))
            status = _kernel32.WaitForSingleObject(self._handle, milliseconds)
            if status == _WAIT_TIMEOUT:
                raise subprocess.TimeoutExpired(["owned-process"], timeout)
            if status == _WAIT_FAILED or status != _WAIT_OBJECT_0:
                raise OwnedProcessError(
                    "WaitForSingleObject failed with "
                    f"status=0x{status:08x} error={ctypes.get_last_error()}"
                )
            result = self.poll()
            if result is None:
                raise OwnedProcessError(
                    "process remained STILL_ACTIVE after signaled wait"
                )
            return result

        def communicate(self, timeout: float | None = None) -> tuple[None, None]:
            self.wait(timeout=timeout)
            return None, None

        def kill(self) -> None:
            if self.poll() is not None:
                return
            if not _kernel32.TerminateProcess(self._handle, 70):
                raise OwnedProcessError(
                    "TerminateProcess failed with "
                    f"{ctypes.get_last_error()}"
                )

        terminate = kill

        def close_handle(self) -> None:
            if self._closed:
                return
            _close_handle(self._handle, label="process")
            self._closed = True


    def _create_windows_process(
        command: Sequence[str],
        *,
        stdin: BinaryIO,
        stdout: BinaryIO,
        stderr: BinaryIO,
        cwd: str,
        env: Mapping[str, str] | None,
    ) -> tuple[_WindowsCreatedProcess, Any]:
        startup = _STARTUPINFOW()
        startup.cb = ctypes.sizeof(startup)
        startup.dwFlags = _STARTF_USESTDHANDLES
        duplicates: list[Any] = []
        information = _PROCESS_INFORMATION()
        try:
            startup.hStdInput = _duplicate_inheritable_stream(
                stdin, label="stdin"
            )
            duplicates.append(startup.hStdInput)
            startup.hStdOutput = _duplicate_inheritable_stream(
                stdout, label="stdout"
            )
            duplicates.append(startup.hStdOutput)
            startup.hStdError = _duplicate_inheritable_stream(
                stderr, label="stderr"
            )
            duplicates.append(startup.hStdError)
            command_line = ctypes.create_unicode_buffer(
                subprocess.list2cmdline(list(command))
            )
            environment = _environment_block(env)
            flags = (
                _CREATE_SUSPENDED
                | _CREATE_NEW_PROCESS_GROUP
                | _CREATE_UNICODE_ENVIRONMENT
            )
            if not _kernel32.CreateProcessW(
                command[0],
                command_line,
                None,
                None,
                True,
                flags,
                environment,
                cwd,
                ctypes.byref(startup),
                ctypes.byref(information),
            ):
                raise OwnedProcessError(
                    f"CreateProcessW failed with {ctypes.get_last_error()}"
                )
        finally:
            close_failure: OwnedProcessError | None = None
            for duplicate in duplicates:
                try:
                    _close_handle(duplicate, label="inherited std stream")
                except OwnedProcessError as error:
                    close_failure = close_failure or error
            if close_failure is not None:
                if information.hProcess:
                    _kernel32.TerminateProcess(information.hProcess, 70)
                    _kernel32.CloseHandle(information.hProcess)
                if information.hThread:
                    _kernel32.CloseHandle(information.hThread)
                raise close_failure
        return (
            _WindowsCreatedProcess(
                information.hProcess, int(information.dwProcessId)
            ),
            information.hThread,
        )


    def _assign_process_to_job(job: Any, process_handle: Any) -> bool:
        """Small deterministic seam for the pre-assignment failure test."""

        return bool(_kernel32.AssignProcessToJobObject(job, process_handle))


class OwnedProcess:
    """One wrapper process and every descendant it creates."""

    def __init__(
        self,
        process: Any,
        *,
        containment: str,
        job_handle: Any | None = None,
        created_suspended: bool = False,
        resumed_primary_thread: bool = False,
    ) -> None:
        self.process = process
        self._containment = containment
        self._job_handle = job_handle
        self._created_suspended = created_suspended
        self._resumed_primary_thread = resumed_primary_thread
        self._termination_requested = False
        self._active_zero = False
        self._closed = False

    @classmethod
    def launch(
        cls,
        command: Sequence[str],
        *,
        stdin: BinaryIO,
        stdout: BinaryIO,
        stderr: BinaryIO,
        cwd: str,
        env: Mapping[str, str] | None = None,
        process_factory: Any | None = None,
    ) -> "OwnedProcess":
        if not command or any(
            not isinstance(part, str) or not part for part in command
        ):
            raise ValueError(
                "owned process command must contain non-empty strings"
            )
        if os.name != "nt":
            factory = process_factory or subprocess.Popen
            process = factory(
                list(command),
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                cwd=cwd,
                env=dict(env) if env is not None else None,
                start_new_session=True,
            )
            return cls(process, containment="posix-process-group")
        if process_factory is not None:
            raise OwnedProcessError(
                "custom process factories cannot preserve Windows "
                "CreateProcessW/primary-thread containment"
            )

        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OwnedProcessError(
                f"CreateJobObjectW failed with {ctypes.get_last_error()}"
            )
        limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not _kernel32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.get_last_error()
            _kernel32.CloseHandle(job)
            raise OwnedProcessError(
                f"SetInformationJobObject failed with {error}"
            )

        process: Any | None = None
        primary_thread: Any | None = None
        assigned = False
        try:
            process, primary_thread = _create_windows_process(
                command,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                cwd=cwd,
                env=env,
            )
            if not _assign_process_to_job(job, process._handle):
                raise OwnedProcessError(
                    "AssignProcessToJobObject failed with "
                    f"{ctypes.get_last_error()}"
                )
            assigned = True
            previous_count = _kernel32.ResumeThread(primary_thread)
            if previous_count == 0xFFFFFFFF:
                raise OwnedProcessError(
                    f"ResumeThread failed with {ctypes.get_last_error()}"
                )
            if previous_count != 1:
                raise OwnedProcessError(
                    "primary thread suspend count was not exactly one"
                )
            _close_handle(primary_thread, label="primary thread")
            primary_thread = None
            return cls(
                process,
                containment="windows-job-object",
                job_handle=job,
                created_suspended=True,
                resumed_primary_thread=True,
            )
        except BaseException as original:
            cleanup_failure: BaseException | None = None
            if process is not None:
                if assigned:
                    if not _kernel32.TerminateJobObject(job, 70):
                        cleanup_failure = OwnedProcessError(
                            "TerminateJobObject failed during launch cleanup "
                            f"with {ctypes.get_last_error()}"
                        )
                elif not _kernel32.TerminateProcess(process._handle, 70):
                    cleanup_failure = OwnedProcessError(
                        "TerminateProcess failed for unassigned suspended "
                        f"wrapper with {ctypes.get_last_error()}"
                    )
                try:
                    process.wait(timeout=5.0)
                    if process.returncode != 70:
                        cleanup_failure = cleanup_failure or OwnedProcessError(
                            "failed launch cleanup returned an unexpected "
                            "wrapper exit code"
                        )
                except BaseException as error:
                    cleanup_failure = cleanup_failure or error
                try:
                    process.close_handle()
                except BaseException as error:
                    cleanup_failure = cleanup_failure or error
            if primary_thread is not None:
                try:
                    _close_handle(primary_thread, label="primary thread")
                except BaseException as error:
                    cleanup_failure = cleanup_failure or error
            if assigned:
                accounting = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
                if not _kernel32.QueryInformationJobObject(
                    job,
                    _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                    ctypes.byref(accounting),
                    ctypes.sizeof(accounting),
                    None,
                ):
                    cleanup_failure = cleanup_failure or OwnedProcessError(
                        "QueryInformationJobObject failed during launch "
                        f"cleanup with {ctypes.get_last_error()}"
                    )
                elif accounting.ActiveProcesses != 0:
                    cleanup_failure = cleanup_failure or OwnedProcessError(
                        "failed launch cleanup did not reach "
                        "active-process-zero"
                    )
            try:
                _close_handle(job, label="job")
            except BaseException as error:
                cleanup_failure = cleanup_failure or error
            if cleanup_failure is not None:
                raise OwnedProcessError(
                    "failed launch could not prove complete cleanup"
                ) from cleanup_failure
            raise original

    def _active_processes(self) -> int:
        if os.name != "nt":
            if self.process.poll() is None:
                return 1
            try:
                os.killpg(os.getpgid(self.process.pid), 0)
            except (ProcessLookupError, PermissionError, OSError):
                return 0
            return 1
        if self._job_handle is None:
            raise OwnedProcessError("Windows Job Object handle is absent")
        accounting = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        if not _kernel32.QueryInformationJobObject(
            self._job_handle,
            _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(accounting),
            ctypes.sizeof(accounting),
            None,
        ):
            raise OwnedProcessError(
                "QueryInformationJobObject failed with "
                f"{ctypes.get_last_error()}"
            )
        return int(accounting.ActiveProcesses)

    def wait_active_zero(self, *, timeout: float) -> None:
        deadline = time.monotonic() + _positive_timeout(
            timeout, label="active-process-zero timeout"
        )
        while True:
            if self._active_processes() == 0:
                self._active_zero = True
                return
            if time.monotonic() >= deadline:
                raise OwnedProcessError(
                    "owned process tree did not reach active-process-zero"
                )
            time.sleep(0.01)

    def terminate_all(self, *, timeout: float) -> None:
        self._termination_requested = True
        if os.name == "nt":
            if self._job_handle is None or not _kernel32.TerminateJobObject(
                self._job_handle, 70
            ):
                raise OwnedProcessError(
                    "TerminateJobObject failed with "
                    f"{ctypes.get_last_error()}"
                )
        else:  # pragma: no cover - production is Windows
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            self.process.wait(
                timeout=_positive_timeout(timeout, label="kill timeout")
            )
        except (subprocess.TimeoutExpired, TimeoutError) as error:
            raise OwnedProcessError(
                "owned wrapper did not exit after tree termination"
            ) from error
        self.wait_active_zero(timeout=timeout)

    def prove_natural_zero(self, *, timeout: float) -> None:
        """Require wrapper and descendants to end without forced cleanup."""

        if self.process.poll() is None:
            raise OwnedProcessError("wrapper is still active")
        self.wait_active_zero(timeout=timeout)

    def evidence(self) -> OwnedProcessEvidence:
        return OwnedProcessEvidence(
            schema="atomic-e00-owned-process-v1",
            containment=self._containment,
            kill_on_close=os.name == "nt",
            created_suspended_before_assignment=self._created_suspended,
            resumed_primary_thread=self._resumed_primary_thread,
            active_processes_zero=self._active_zero,
            termination_requested=self._termination_requested,
        )

    def close(self) -> None:
        if self._closed:
            return
        failure: BaseException | None = None
        try:
            if not self._active_zero:
                self.terminate_all(timeout=5.0)
        except BaseException as error:
            failure = error
        try:
            if os.name == "nt":
                if hasattr(self.process, "close_handle"):
                    self.process.close_handle()
                if self._job_handle is not None:
                    _close_handle(self._job_handle, label="job")
            self._closed = True
        except BaseException as error:
            failure = failure or error
        if failure is not None:
            raise OwnedProcessError(
                "owned process close did not complete cleanly"
            ) from failure

    def __enter__(self) -> "OwnedProcess":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


__all__ = [
    "OwnedProcess",
    "OwnedProcessError",
    "OwnedProcessEvidence",
]
