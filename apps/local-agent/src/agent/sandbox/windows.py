"""不依赖外部程序的 Windows Restricted Token 沙盒。"""

from __future__ import annotations

import asyncio
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
from typing import Any

from agent.permissions import SandboxMode
from agent.sandbox.config import SandboxNetwork


_IS_WINDOWS = os.name == "nt"
_STATE_LOCK = threading.Lock()
_CAPABILITY_SID = re.compile(r"S-1-5-21-(?:[0-9]{1,10}-){3}[0-9]{1,10}\Z")


def is_available() -> bool:
    return _IS_WINDOWS


async def spawn(
    command: str,
    *,
    cwd: Path,
    state_dir: Path,
    mode: SandboxMode,
    environment: dict[str, str],
    network: SandboxNetwork = SandboxNetwork.HOST,
) -> "WindowsSandboxProcess":
    """在线程中准备 ACL 和 token，避免 Windows 安全 API 阻塞事件循环。"""

    if not _IS_WINDOWS:
        raise RuntimeError("Windows 原生沙盒只支持 Windows")
    return await asyncio.to_thread(
        _spawn_sync,
        command,
        cwd.resolve(),
        state_dir.resolve(),
        mode,
        environment,
        network,
    )


class WindowsSandboxProcess:
    """提供进程管理器所需的增量读写表面。"""

    def __init__(
        self, process: int, job: int, stdout_read: int, stdin_write: int
    ) -> None:
        self._process = process
        self._job = job
        self._stdout_read = stdout_read
        self._stdin_write = stdin_write
        self._lock = threading.Lock()
        self._communicate_task: asyncio.Task[tuple[bytes, None]] | None = None
        self._wait_task: asyncio.Task[int] | None = None
        self.returncode: int | None = None

    async def communicate(self) -> tuple[bytes, None]:
        if self._communicate_task is None:
            self._communicate_task = asyncio.create_task(
                self._communicate(), name="windows-sandbox-output"
            )
        # wait_for() 会取消外层等待；保留收集任务，kill 后仍能回收句柄。
        return await asyncio.shield(self._communicate_task)

    async def read(self, size: int = 65_536) -> bytes:
        return await asyncio.to_thread(self._read, size)

    async def write(self, data: bytes) -> None:
        if data:
            await asyncio.to_thread(self._write, data)

    async def wait(self) -> int:
        if self._wait_task is None:
            self._wait_task = asyncio.create_task(
                asyncio.to_thread(self._wait), name="windows-sandbox-wait"
            )
        return await asyncio.shield(self._wait_task)

    def kill(self) -> None:
        with self._lock:
            if self._job:
                if not _kernel32.TerminateJobObject(self._job, 1):
                    raise _winerror("TerminateJobObject")

    async def _communicate(self) -> tuple[bytes, None]:
        chunks: list[bytes] = []
        while chunk := await self.read():
            chunks.append(chunk)
        await self.wait()
        return b"".join(chunks), None

    def _read(self, size: int) -> bytes:
        with self._lock:
            handle = self._stdout_read
        if not handle:
            return b""
        buffer = ctypes.create_string_buffer(size)
        read = wintypes.DWORD()
        if _kernel32.ReadFile(handle, buffer, size, ctypes.byref(read), None):
            return buffer.raw[: read.value]
        error = ctypes.get_last_error()
        if error not in {_ERROR_BROKEN_PIPE, _ERROR_HANDLE_EOF}:
            raise _winerror("ReadFile", error)
        with self._lock:
            if self._stdout_read == handle:
                _kernel32.CloseHandle(handle)
                self._stdout_read = 0
        return b""

    def _write(self, data: bytes) -> None:
        with self._lock:
            handle = self._stdin_write
        if not handle:
            raise BrokenPipeError("进程 stdin 已关闭")
        buffer = ctypes.create_string_buffer(data)
        offset = 0
        while offset < len(data):
            written = wintypes.DWORD()
            if not _kernel32.WriteFile(
                handle,
                ctypes.byref(buffer, offset),
                len(data) - offset,
                ctypes.byref(written),
                None,
            ):
                error = ctypes.get_last_error()
                if error in {_ERROR_BROKEN_PIPE, _ERROR_NO_DATA}:
                    raise BrokenPipeError(error, "进程 stdin 已关闭")
                raise _winerror("WriteFile", error)
            offset += written.value

    def _wait(self) -> int:
        with self._lock:
            handle = self._process
        if not handle:
            assert self.returncode is not None
            return self.returncode
        if _kernel32.WaitForSingleObject(handle, _INFINITE) == _WAIT_FAILED:
            raise _winerror("WaitForSingleObject")
        exit_code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise _winerror("GetExitCodeProcess")
        self.returncode = _signed_exit_code(exit_code.value)
        with self._lock:
            # 等待结束后子进程不会再读取 stdin；父进程侧句柄可以安全关闭。
            for handle_name in ("_stdin_write", "_process", "_job"):
                owned_handle = getattr(self, handle_name)
                if owned_handle:
                    _kernel32.CloseHandle(owned_handle)
                    setattr(self, handle_name, 0)
        return self.returncode

    def __del__(self) -> None:
        # 仅兜底关闭未进入正常 wait/read 路径的父进程侧句柄；Job 关闭会终止进程树。
        if not _IS_WINDOWS:
            return
        try:
            with self._lock:
                for handle_name in (
                    "_stdin_write",
                    "_stdout_read",
                    "_process",
                    "_job",
                ):
                    handle = getattr(self, handle_name)
                    if handle:
                        _kernel32.CloseHandle(handle)
                        setattr(self, handle_name, 0)
        except Exception:
            pass


if _IS_WINDOWS:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _userenv = ctypes.WinDLL("userenv", use_last_error=True)

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]

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
            ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
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

    class _STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [
            ("StartupInfo", _STARTUPINFOW),
            ("lpAttributeList", wintypes.LPVOID),
        ]

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]

    class _TOKEN_USER(ctypes.Structure):
        _fields_ = [("User", _SID_AND_ATTRIBUTES)]

    class _SECURITY_CAPABILITIES(ctypes.Structure):
        _fields_ = [
            ("AppContainerSid", wintypes.LPVOID),
            ("Capabilities", ctypes.POINTER(_SID_AND_ATTRIBUTES)),
            ("CapabilityCount", wintypes.DWORD),
            ("Reserved", wintypes.DWORD),
        ]

    class _TRUSTEE_W(ctypes.Structure):
        _fields_ = [
            ("pMultipleTrustee", wintypes.LPVOID),
            ("MultipleTrusteeOperation", ctypes.c_int),
            ("TrusteeForm", ctypes.c_int),
            ("TrusteeType", ctypes.c_int),
            ("ptstrName", wintypes.LPVOID),
        ]

    class _EXPLICIT_ACCESS_W(ctypes.Structure):
        _fields_ = [
            ("grfAccessPermissions", wintypes.DWORD),
            ("grfAccessMode", ctypes.c_int),
            ("grfInheritance", wintypes.DWORD),
            ("Trustee", _TRUSTEE_W),
        ]

    class _TOKEN_DEFAULT_DACL(ctypes.Structure):
        _fields_ = [("DefaultDacl", wintypes.LPVOID)]

    class _LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class _LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", _LUID), ("Attributes", wintypes.DWORD)]

    class _TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [
            ("PrivilegeCount", wintypes.DWORD),
            ("Privileges", _LUID_AND_ATTRIBUTES * 1),
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

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

def _configure_winapi() -> None:
    """集中声明指针宽度；错误的默认 c_int 会在 64 位 Windows 截断句柄。"""

    if not _IS_WINDOWS:
        return
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    _kernel32.LocalFree.restype = wintypes.HLOCAL
    _kernel32.InitializeProcThreadAttributeList.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    _kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    _kernel32.UpdateProcThreadAttribute.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.c_size_t,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    _kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    _kernel32.DeleteProcThreadAttributeList.argtypes = [wintypes.LPVOID]
    _kernel32.DeleteProcThreadAttributeList.restype = None
    _kernel32.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
        wintypes.DWORD,
    ]
    _kernel32.CreatePipe.restype = wintypes.BOOL
    _kernel32.SetHandleInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _kernel32.SetHandleInformation.restype = wintypes.BOOL
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    _kernel32.ReadFile.restype = wintypes.BOOL
    _kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    _kernel32.WriteFile.restype = wintypes.BOOL
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateProcess.restype = wintypes.BOOL

    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.CreateRestrictedToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_SID_AND_ATTRIBUTES),
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_SID_AND_ATTRIBUTES),
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.CreateRestrictedToken.restype = wintypes.BOOL
    _advapi32.ConvertStringSidToSidW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    _advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    _advapi32.CreateWellKnownSid.argtypes = [
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.CreateWellKnownSid.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.GetLengthSid.argtypes = [wintypes.LPVOID]
    _advapi32.GetLengthSid.restype = wintypes.DWORD
    _advapi32.FreeSid.argtypes = [wintypes.LPVOID]
    _advapi32.FreeSid.restype = wintypes.LPVOID
    _advapi32.SetEntriesInAclW.argtypes = [
        wintypes.ULONG,
        ctypes.POINTER(_EXPLICIT_ACCESS_W),
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    _advapi32.SetEntriesInAclW.restype = wintypes.DWORD
    _advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPVOID),
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    _advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    _advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.SetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _advapi32.SetTokenInformation.restype = wintypes.BOOL
    _advapi32.LookupPrivilegeValueW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.POINTER(_LUID),
    ]
    _advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
    _advapi32.AdjustTokenPrivileges.argtypes = [
        wintypes.HANDLE,
        wintypes.BOOL,
        ctypes.POINTER(_TOKEN_PRIVILEGES),
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    _advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL
    _advapi32.CreateProcessAsUserW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOW),
        ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    _advapi32.CreateProcessAsUserW.restype = wintypes.BOOL
    _userenv.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.POINTER(_SID_AND_ATTRIBUTES),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    _userenv.CreateAppContainerProfile.restype = wintypes.LONG
    _userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    _userenv.DeriveAppContainerSidFromAppContainerName.restype = wintypes.LONG
    _userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    _userenv.DeleteAppContainerProfile.restype = wintypes.LONG


def _spawn_sync(
    command: str,
    cwd: Path,
    state_dir: Path,
    mode: SandboxMode,
    environment: dict[str, str],
    network: SandboxNetwork,
) -> WindowsSandboxProcess:
    if mode not in {SandboxMode.READ_ONLY, SandboxMode.WORKSPACE_WRITE}:
        raise ValueError(f"Windows 沙盒不支持模式: {mode.value}")
    if not cwd.is_dir():
        raise ValueError(f"沙盒工作目录不存在: {cwd}")
    if state_dir.is_relative_to(cwd):
        raise ValueError("沙盒状态目录必须位于模型可写工作区之外")

    appcontainer_sid = wintypes.LPVOID()
    restricted_token = 0
    try:
        with _STATE_LOCK:
            capability = _capability_sid(state_dir, cwd, mode)
            workspace_id = hashlib.sha256(_path_key(cwd).encode("utf-8")).hexdigest()[:16]
            sandbox_tmp = state_dir / "tmp" / workspace_id
            sandbox_tmp.mkdir(parents=True, exist_ok=True)
            if network is SandboxNetwork.BLOCKED:
                appcontainer_sid = _appcontainer_sid(_appcontainer_name(cwd, mode))
            user_buffer = ctypes.create_string_buffer(_current_user_sid_bytes())
            user_sid = ctypes.addressof(user_buffer)
            with _local_sid(capability) as capability_sid:
                _set_path_ace(
                    sandbox_tmp, capability_sid, deny=False, normal_sid=user_sid
                )
                if network is SandboxNetwork.BLOCKED:
                    _set_path_ace(sandbox_tmp, appcontainer_sid.value, deny=False)
                    # AppContainer needs this write-shaped directory grant to launch child
                    # processes from cwd; the Restricted Token SID remains read-only below.
                    _set_path_ace(cwd, appcontainer_sid.value, deny=False)
                if (
                    mode is SandboxMode.READ_ONLY
                    and network is SandboxNetwork.BLOCKED
                ):
                    _set_path_ace(
                        cwd,
                        capability_sid,
                        deny=False,
                        normal_sid=user_sid,
                        read_only=True,
                    )
                elif mode is SandboxMode.WORKSPACE_WRITE:
                    _set_path_ace(cwd, capability_sid, deny=False, normal_sid=user_sid)
                    git_dir = cwd / ".git"
                    if git_dir.exists():
                        _set_path_ace(git_dir, capability_sid, deny=True)
                        if network is SandboxNetwork.BLOCKED:
                            _set_path_ace(git_dir, appcontainer_sid.value, deny=True)

                restricted_token = _create_restricted_token(capability_sid)

        environment = dict(environment)
        environment["TEMP"] = str(sandbox_tmp)
        environment["TMP"] = str(sandbox_tmp)
        return _create_process(
            restricted_token,
            command,
            cwd,
            environment,
            appcontainer_sid.value if network is SandboxNetwork.BLOCKED else None,
        )
    finally:
        if restricted_token:
            _kernel32.CloseHandle(restricted_token)
        if network is SandboxNetwork.BLOCKED and appcontainer_sid:
            _advapi32.FreeSid(appcontainer_sid)


def _create_restricted_token(capability_sid: int) -> int:
    desired = (
        _TOKEN_ASSIGN_PRIMARY
        | _TOKEN_DUPLICATE
        | _TOKEN_QUERY
        | _TOKEN_ADJUST_DEFAULT
        | _TOKEN_ADJUST_SESSIONID
        | _TOKEN_ADJUST_PRIVILEGES
    )
    base = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(
        _kernel32.GetCurrentProcess(), desired, ctypes.byref(base)
    ):
        raise _winerror("OpenProcessToken")
    try:
        logon_buffer = ctypes.create_string_buffer(_logon_sid_bytes(base.value))
        logon_sid = ctypes.addressof(logon_buffer)
        everyone_buffer = ctypes.create_string_buffer(_well_known_sid(_WIN_WORLD_SID))
        everyone_sid = ctypes.addressof(everyone_buffer)
        restricting = (_SID_AND_ATTRIBUTES * 3)(
            _SID_AND_ATTRIBUTES(capability_sid, 0),
            _SID_AND_ATTRIBUTES(logon_sid, 0),
            _SID_AND_ATTRIBUTES(everyone_sid, 0),
        )
        # ponytail: 该 MVP 尚未审计任意 world-writable 目录；在宣称完整磁盘写隔离前，
        # 增加拒绝 ACE 扫描。Everyone 目前用于保留常规系统路径的可读性。
        token = wintypes.HANDLE()
        if not _advapi32.CreateRestrictedToken(
            base,
            _DISABLE_MAX_PRIVILEGE | _LUA_TOKEN | _WRITE_RESTRICTED,
            0,
            None,
            0,
            None,
            len(restricting),
            restricting,
            ctypes.byref(token),
        ):
            raise _winerror("CreateRestrictedToken")
        try:
            _set_default_dacl(token.value, [capability_sid, logon_sid, everyone_sid])
            _enable_change_notify(token.value)
            return token.value
        except Exception:
            _kernel32.CloseHandle(token)
            raise
    finally:
        _kernel32.CloseHandle(base)


def _create_process(
    token: int,
    command: str,
    cwd: Path,
    environment: dict[str, str],
    appcontainer_sid: int | None,
) -> WindowsSandboxProcess:
    security = _SECURITY_ATTRIBUTES(
        ctypes.sizeof(_SECURITY_ATTRIBUTES), None, True
    )
    stdout_read = wintypes.HANDLE()
    stdout_write = wintypes.HANDLE()
    stdin_read = wintypes.HANDLE()
    stdin_write = wintypes.HANDLE()
    process_info = _PROCESS_INFORMATION()
    job = wintypes.HANDLE()
    attribute_list: ctypes.Array[ctypes.c_char] | None = None
    success = False
    if not _kernel32.CreatePipe(
        ctypes.byref(stdout_read), ctypes.byref(stdout_write), ctypes.byref(security), 0
    ):
        raise _winerror("CreatePipe(stdout)")
    try:
        if not _kernel32.SetHandleInformation(
            stdout_read, _HANDLE_FLAG_INHERIT, 0
        ):
            raise _winerror("SetHandleInformation")
        if not _kernel32.CreatePipe(
            ctypes.byref(stdin_read), ctypes.byref(stdin_write), ctypes.byref(security), 0
        ):
            raise _winerror("CreatePipe(stdin)")
        if not _kernel32.SetHandleInformation(stdin_write, _HANDLE_FLAG_INHERIT, 0):
            raise _winerror("SetHandleInformation(stdin)")

        startup = _STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput = stdin_read
        startup.StartupInfo.hStdOutput = stdout_write
        startup.StartupInfo.hStdError = stdout_write
        attribute_size = ctypes.c_size_t()
        attribute_count = 4 if appcontainer_sid is not None else 1
        ctypes.set_last_error(0)
        _kernel32.InitializeProcThreadAttributeList(
            None, attribute_count, 0, ctypes.byref(attribute_size)
        )
        if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
            raise _winerror("InitializeProcThreadAttributeList(size)")
        attribute_list = ctypes.create_string_buffer(attribute_size.value)
        if not _kernel32.InitializeProcThreadAttributeList(
            attribute_list, attribute_count, 0, ctypes.byref(attribute_size)
        ):
            raise _winerror("InitializeProcThreadAttributeList")
        startup.lpAttributeList = ctypes.addressof(attribute_list)
        inherited_handles = (wintypes.HANDLE * 2)(stdout_write, stdin_read)
        if not _kernel32.UpdateProcThreadAttribute(
            attribute_list,
            0,
            _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            inherited_handles,
            ctypes.sizeof(inherited_handles),
            None,
            None,
        ):
            raise _winerror("UpdateProcThreadAttribute(HandleList)")
        if appcontainer_sid is not None:
            security_capabilities = _SECURITY_CAPABILITIES(
                appcontainer_sid, None, 0, 0
            )
            if not _kernel32.UpdateProcThreadAttribute(
                attribute_list,
                0,
                _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                ctypes.byref(security_capabilities),
                ctypes.sizeof(security_capabilities),
                None,
                None,
            ):
                raise _winerror("UpdateProcThreadAttribute(SecurityCapabilities)")
            desktop_policy = wintypes.DWORD(
                _PROCESS_CREATION_DESKTOP_APP_BREAKAWAY_DISABLE_PROCESS_TREE
            )
            if not _kernel32.UpdateProcThreadAttribute(
                attribute_list,
                0,
                _PROC_THREAD_ATTRIBUTE_DESKTOP_APP_POLICY,
                ctypes.byref(desktop_policy),
                ctypes.sizeof(desktop_policy),
                None,
                None,
            ):
                raise _winerror("UpdateProcThreadAttribute(DesktopAppPolicy)")
            child_policy = wintypes.DWORD(_PROCESS_CREATION_CHILD_PROCESS_OVERRIDE)
            if not _kernel32.UpdateProcThreadAttribute(
                attribute_list,
                0,
                _PROC_THREAD_ATTRIBUTE_CHILD_PROCESS_POLICY,
                ctypes.byref(child_policy),
                ctypes.sizeof(child_policy),
                None,
                None,
            ):
                raise _winerror("UpdateProcThreadAttribute(ChildProcessPolicy)")
        executable = str(
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32"
            / "cmd.exe"
        )
        # cmd.exe owns the command tail; CRT-style list2cmdline would turn inner quotes into
        # backslash-escaped quotes, which cmd treats as literal path characters.
        command_line = ctypes.create_unicode_buffer(
            f'"{executable}" /d /s /c "{command}"'
        )
        env_block = ctypes.create_unicode_buffer(_environment_block(environment))
        flags = (
            _CREATE_SUSPENDED
            | _CREATE_UNICODE_ENVIRONMENT
            | _CREATE_NO_WINDOW
            | _EXTENDED_STARTUPINFO_PRESENT
        )
        if not _advapi32.CreateProcessAsUserW(
            token,
            executable,
            command_line,
            None,
            None,
            True,
            flags,
            env_block,
            str(cwd),
            ctypes.cast(ctypes.byref(startup), ctypes.POINTER(_STARTUPINFOW)),
            ctypes.byref(process_info),
        ):
            raise _winerror("CreateProcessAsUserW")

        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            raise _winerror("CreateJobObjectW")
        limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        )
        if not _kernel32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise _winerror("SetInformationJobObject")
        if not _kernel32.AssignProcessToJobObject(job, process_info.hProcess):
            raise _winerror("AssignProcessToJobObject")
        if _kernel32.ResumeThread(process_info.hThread) == _DWORD_MAX:
            raise _winerror("ResumeThread")

        _kernel32.CloseHandle(process_info.hThread)
        process_info.hThread = None
        _kernel32.CloseHandle(stdout_write)
        stdout_write = None
        _kernel32.CloseHandle(stdin_read)
        stdin_read = None
        result = WindowsSandboxProcess(
            process_info.hProcess, job, stdout_read.value, stdin_write.value
        )
        stdin_write = None
        success = True
        return result
    except Exception:
        if process_info.hProcess:
            _kernel32.TerminateProcess(process_info.hProcess, 1)
        raise
    finally:
        if attribute_list is not None:
            _kernel32.DeleteProcThreadAttributeList(attribute_list)
        if not success:
            for handle in (
                process_info.hThread,
                process_info.hProcess,
                stdout_read,
                stdout_write,
                stdin_read,
                stdin_write,
                job,
            ):
                if handle and handle != _INVALID_HANDLE_VALUE:
                    _kernel32.CloseHandle(handle)


class _local_sid:
    def __init__(self, sid: str) -> None:
        self._sid = sid
        self.pointer = wintypes.LPVOID()

    def __enter__(self) -> int:
        if not _advapi32.ConvertStringSidToSidW(
            self._sid, ctypes.byref(self.pointer)
        ):
            raise _winerror("ConvertStringSidToSidW")
        return self.pointer.value

    def __exit__(self, *_: object) -> None:
        if self.pointer:
            _kernel32.LocalFree(self.pointer)


def _set_path_ace(
    path: Path,
    sid: int,
    *,
    deny: bool,
    normal_sid: int | None = None,
    read_only: bool = False,
) -> None:
    old_acl = wintypes.LPVOID()
    descriptor = wintypes.LPVOID()
    mutable_path = ctypes.create_unicode_buffer(str(path))
    code = _advapi32.GetNamedSecurityInfoW(
        mutable_path,
        _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.byref(old_acl),
        None,
        ctypes.byref(descriptor),
    )
    if code != _ERROR_SUCCESS:
        raise OSError(code, f"GetNamedSecurityInfoW 失败: {path}")
    new_acl = wintypes.LPVOID()
    try:
        entries = [
            _EXPLICIT_ACCESS_W(
                _DENY_WRITE_MASK
                if deny
                else (_READ_ALLOW_MASK if read_only else _WRITE_ALLOW_MASK),
                _DENY_ACCESS if deny else _SET_ACCESS,
                _SUB_CONTAINERS_AND_OBJECTS_INHERIT,
                _TRUSTEE_W(None, 0, _TRUSTEE_IS_SID, _TRUSTEE_IS_UNKNOWN, sid),
            )
        ]
        if not deny and normal_sid is not None:
            # Restricted token access checks run once with normal SIDs and once with
            # restricting SIDs. Some owner-only temp directories need an explicit user ACE
            # for the first pass even though the host user already owns the directory.
            entries.append(
                _EXPLICIT_ACCESS_W(
                    _READ_ALLOW_MASK if read_only else _WRITE_ALLOW_MASK,
                    _GRANT_ACCESS,
                    _SUB_CONTAINERS_AND_OBJECTS_INHERIT,
                    _TRUSTEE_W(
                        None,
                        0,
                        _TRUSTEE_IS_SID,
                        _TRUSTEE_IS_UNKNOWN,
                        normal_sid,
                    ),
                )
            )
        entries_array = (_EXPLICIT_ACCESS_W * len(entries))(*entries)
        code = _advapi32.SetEntriesInAclW(
            len(entries_array), entries_array, old_acl, ctypes.byref(new_acl)
        )
        if code != _ERROR_SUCCESS:
            raise OSError(code, f"SetEntriesInAclW 失败: {path}")
        code = _advapi32.SetNamedSecurityInfoW(
            mutable_path,
            _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION,
            None,
            None,
            new_acl,
            None,
        )
        if code != _ERROR_SUCCESS:
            raise OSError(code, f"SetNamedSecurityInfoW 失败: {path}")
    finally:
        if new_acl:
            _kernel32.LocalFree(new_acl)
        if descriptor:
            _kernel32.LocalFree(descriptor)


def _set_default_dacl(token: int, sids: list[int]) -> None:
    entries = (_EXPLICIT_ACCESS_W * len(sids))(
        *(
            _EXPLICIT_ACCESS_W(
                _GENERIC_ALL,
                _GRANT_ACCESS,
                0,
                _TRUSTEE_W(None, 0, _TRUSTEE_IS_SID, _TRUSTEE_IS_UNKNOWN, sid),
            )
            for sid in sids
        )
    )
    acl = wintypes.LPVOID()
    code = _advapi32.SetEntriesInAclW(len(entries), entries, None, ctypes.byref(acl))
    if code != _ERROR_SUCCESS:
        raise OSError(code, "SetEntriesInAclW(default DACL) 失败")
    try:
        info = _TOKEN_DEFAULT_DACL(acl)
        if not _advapi32.SetTokenInformation(
            token,
            _TOKEN_DEFAULT_DACL_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            raise _winerror("SetTokenInformation(TokenDefaultDacl)")
    finally:
        _kernel32.LocalFree(acl)


def _enable_change_notify(token: int) -> None:
    luid = _LUID()
    if not _advapi32.LookupPrivilegeValueW(
        None, "SeChangeNotifyPrivilege", ctypes.byref(luid)
    ):
        raise _winerror("LookupPrivilegeValueW")
    privileges = _TOKEN_PRIVILEGES(
        1, (_LUID_AND_ATTRIBUTES * 1)(_LUID_AND_ATTRIBUTES(luid, _SE_PRIVILEGE_ENABLED))
    )
    ctypes.set_last_error(0)
    if not _advapi32.AdjustTokenPrivileges(
        token, False, ctypes.byref(privileges), 0, None, None
    ):
        raise _winerror("AdjustTokenPrivileges")
    if ctypes.get_last_error() != _ERROR_SUCCESS:
        raise _winerror("AdjustTokenPrivileges")


def _logon_sid_bytes(token: int) -> bytes:
    needed = wintypes.DWORD()
    _advapi32.GetTokenInformation(token, _TOKEN_GROUPS, None, 0, ctypes.byref(needed))
    if not needed.value:
        raise _winerror("GetTokenInformation(TokenGroups)")
    buffer = ctypes.create_string_buffer(needed.value)
    if not _advapi32.GetTokenInformation(
        token, _TOKEN_GROUPS, buffer, len(buffer), ctypes.byref(needed)
    ):
        raise _winerror("GetTokenInformation(TokenGroups)")
    count = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value
    offset = _align(ctypes.sizeof(wintypes.DWORD), ctypes.alignment(_SID_AND_ATTRIBUTES))
    groups = ctypes.cast(
        ctypes.addressof(buffer) + offset, ctypes.POINTER(_SID_AND_ATTRIBUTES)
    )
    for index in range(count):
        group = groups[index]
        if group.Attributes & _SE_GROUP_LOGON_ID == _SE_GROUP_LOGON_ID:
            length = _advapi32.GetLengthSid(group.Sid)
            if not length:
                raise _winerror("GetLengthSid")
            return ctypes.string_at(group.Sid, length)
    raise RuntimeError("当前 token 中没有 Logon SID")


def _current_user_sid_bytes() -> bytes:
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(
        _kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
    ):
        raise _winerror("OpenProcessToken(TokenUser)")
    try:
        needed = wintypes.DWORD()
        _advapi32.GetTokenInformation(
            token, _TOKEN_USER_CLASS, None, 0, ctypes.byref(needed)
        )
        if not needed.value:
            raise _winerror("GetTokenInformation(TokenUser)")
        buffer = ctypes.create_string_buffer(needed.value)
        if not _advapi32.GetTokenInformation(
            token, _TOKEN_USER_CLASS, buffer, len(buffer), ctypes.byref(needed)
        ):
            raise _winerror("GetTokenInformation(TokenUser)")
        user = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_USER)).contents
        length = _advapi32.GetLengthSid(user.User.Sid)
        if not length:
            raise _winerror("GetLengthSid(TokenUser)")
        return ctypes.string_at(user.User.Sid, length)
    finally:
        _kernel32.CloseHandle(token)


def _well_known_sid(sid_type: int) -> bytes:
    size = wintypes.DWORD(68)
    buffer = ctypes.create_string_buffer(size.value)
    if not _advapi32.CreateWellKnownSid(
        sid_type, None, buffer, ctypes.byref(size)
    ):
        raise _winerror("CreateWellKnownSid")
    return buffer.raw[: size.value]


def _capability_sid(
    state_dir: Path, workspace: Path, mode: SandboxMode
) -> str:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "capabilities.json"
    state: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded
        except (OSError, json.JSONDecodeError):
            # 损坏的权限状态不能被部分信任；下面生成新 SID 并原子覆盖。
            state = {}
    workspaces = state.get("workspaces")
    if not isinstance(workspaces, dict):
        workspaces = {}
        state["workspaces"] = workspaces
    # read-only 绝不能复用曾写入 workspace ACL 的 SID，否则旧 ACE 会把只读 token 重新变成可写。
    key = f"{_path_key(workspace)}|{mode.value}"
    existing = workspaces.get(key)
    if isinstance(existing, str) and _CAPABILITY_SID.fullmatch(existing):
        return existing
    sid = "S-1-5-21-" + "-".join(str(secrets.randbits(32)) for _ in range(4))
    workspaces[key] = sid
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)
    return sid


def _appcontainer_name(workspace: Path, mode: SandboxMode) -> str:
    digest = hashlib.sha256(
        f"{_path_key(workspace)}|{mode.value}".encode("utf-8")
    ).hexdigest()[:24]
    suffix = "ro" if mode is SandboxMode.READ_ONLY else "rw"
    return f"CodeXAgent.Sandbox.{digest}.{suffix}"


def _appcontainer_sid(name: str) -> wintypes.LPVOID:
    sid = wintypes.LPVOID()
    result = _userenv.CreateAppContainerProfile(
        name, "CodeX Agent Sandbox", "CodeX Agent isolated shell", None, 0, ctypes.byref(sid)
    )
    if _unsigned_hresult(result) == _HRESULT_ALREADY_EXISTS:
        result = _userenv.DeriveAppContainerSidFromAppContainerName(
            name, ctypes.byref(sid)
        )
    if result < 0:
        raise OSError(_unsigned_hresult(result), f"创建 AppContainer profile 失败: {name}")
    return sid


def _delete_appcontainer_profile(workspace: Path, mode: SandboxMode) -> None:
    """仅供测试清理临时 profile；生产 profile 必须和持久 ACL 使用相同身份。"""

    result = _userenv.DeleteAppContainerProfile(_appcontainer_name(workspace, mode))
    if result < 0:
        raise OSError(_unsigned_hresult(result), "删除 AppContainer profile 失败")


def _environment_block(environment: dict[str, str]) -> str:
    return "\0".join(
        f"{name}={value}"
        for name, value in sorted(environment.items(), key=lambda item: item[0].upper())
        if "\0" not in name and "=" not in name and "\0" not in value
    ) + "\0\0"


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def _signed_exit_code(value: int) -> int:
    return value - (1 << 32) if value & (1 << 31) else value


def _unsigned_hresult(value: int) -> int:
    return value & 0xFFFFFFFF


def _winerror(action: str, code: int | None = None) -> OSError:
    resolved = ctypes.get_last_error() if code is None else code
    return OSError(resolved, f"{action} 失败: {ctypes.FormatError(resolved).strip()}")


_TOKEN_ASSIGN_PRIMARY = 0x0001
_TOKEN_DUPLICATE = 0x0002
_TOKEN_QUERY = 0x0008
_TOKEN_ADJUST_PRIVILEGES = 0x0020
_TOKEN_ADJUST_DEFAULT = 0x0080
_TOKEN_ADJUST_SESSIONID = 0x0100
_DISABLE_MAX_PRIVILEGE = 0x01
_LUA_TOKEN = 0x04
_WRITE_RESTRICTED = 0x08
_TOKEN_GROUPS = 2
_TOKEN_USER_CLASS = 1
_TOKEN_DEFAULT_DACL_CLASS = 6
_SE_GROUP_LOGON_ID = 0xC0000000
_SE_PRIVILEGE_ENABLED = 0x00000002
_WIN_WORLD_SID = 1

_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x00000004
_ERROR_SUCCESS = 0
_SET_ACCESS = 2
_GRANT_ACCESS = 1
_DENY_ACCESS = 3
_TRUSTEE_IS_SID = 0
_TRUSTEE_IS_UNKNOWN = 0
_SUB_CONTAINERS_AND_OBJECTS_INHERIT = 0x3
_FILE_GENERIC_READ = 0x00120089
_FILE_GENERIC_WRITE = 0x00120116
_FILE_GENERIC_EXECUTE = 0x001200A0
_DELETE = 0x00010000
_FILE_DELETE_CHILD = 0x00000040
_WRITE_ALLOW_MASK = (
    _FILE_GENERIC_READ | _FILE_GENERIC_WRITE | _FILE_GENERIC_EXECUTE | _DELETE
)
_READ_ALLOW_MASK = _FILE_GENERIC_READ | _FILE_GENERIC_EXECUTE
_DENY_WRITE_MASK = _FILE_GENERIC_WRITE | _DELETE | _FILE_DELETE_CHILD
_GENERIC_ALL = 0x10000000
_HANDLE_FLAG_INHERIT = 0x00000001
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_STARTF_USESTDHANDLES = 0x00000100
_CREATE_SUSPENDED = 0x00000004
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_CREATE_NO_WINDOW = 0x08000000
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
_PROC_THREAD_ATTRIBUTE_DESKTOP_APP_POLICY = 0x00020012
_PROC_THREAD_ATTRIBUTE_CHILD_PROCESS_POLICY = 0x0002000E
_PROCESS_CREATION_DESKTOP_APP_BREAKAWAY_DISABLE_PROCESS_TREE = 0x02
_PROCESS_CREATION_CHILD_PROCESS_OVERRIDE = 0x02
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_INFINITE = 0xFFFFFFFF
_WAIT_FAILED = 0xFFFFFFFF
_DWORD_MAX = 0xFFFFFFFF
_ERROR_BROKEN_PIPE = 109
_ERROR_HANDLE_EOF = 38
_ERROR_NO_DATA = 232
_ERROR_INSUFFICIENT_BUFFER = 122
_HRESULT_ALREADY_EXISTS = 0x800700B7


_configure_winapi()
