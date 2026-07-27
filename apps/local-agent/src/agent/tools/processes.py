"""同一 Agent Session 内可续接的本地进程。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import json
from typing import Protocol


MIN_YIELD_TIME_MS = 250
MAX_YIELD_TIME_MS = 30_000
MAX_PROCESSES = 64
MAX_OUTPUT_BYTES = 12_000
HEAD_OUTPUT_BYTES = MAX_OUTPUT_BYTES // 2
TAIL_OUTPUT_BYTES = MAX_OUTPUT_BYTES - HEAD_OUTPUT_BYTES
POST_EXIT_CLOSE_WAIT_SECONDS = 0.05


class ProcessHandle(Protocol):
    """宿主与 Windows 沙盒共同提供的最小进程表面。"""

    async def read(self, size: int = 65_536) -> bytes: ...

    async def write(self, data: bytes) -> None: ...

    async def wait(self) -> int: ...

    def kill(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ProcessResult:
    output: str
    process_id: int | None
    exit_code: int | None
    omitted_bytes: int
    error: str | None = None

    def as_json(self) -> str:
        """两个进程工具共用稳定结果格式，但不互相依赖。"""

        return json.dumps(
            {
                "output": self.output,
                "process_id": self.process_id,
                "exit_code": self.exit_code,
                "omitted_bytes": self.omitted_bytes,
                "error": self.error,
            },
            ensure_ascii=False,
        )


@dataclass(slots=True)
class _ProcessEntry:
    process_id: int
    process: ProcessHandle
    read_only: bool = False
    output: bytearray = field(default_factory=bytearray)
    omitted_bytes: int = 0
    exit_code: int | None = None
    error: str | None = None
    reader_done: bool = False
    process_done: bool = False
    state_changed: asyncio.Event = field(default_factory=asyncio.Event)
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    reader_task: asyncio.Task[None] | None = None
    waiter_task: asyncio.Task[None] | None = None
    timeout_task: asyncio.Task[None] | None = None

    def push_output(self, chunk: bytes) -> None:
        """保留输出首尾；后台读取绝不能因缓冲区已满而阻塞子进程。"""

        self.omitted_bytes = _append_bounded(
            self.output, chunk, self.omitted_bytes
        )
        self.state_changed.set()

    def take_output(self) -> tuple[bytes, int]:
        output = bytes(self.output)
        omitted = self.omitted_bytes
        self.output.clear()
        self.omitted_bytes = 0
        return output, omitted

    def mark_settled(self) -> None:
        self.state_changed.set()
        if self.reader_done and self.process_done:
            self.settled.set()


class ProcessManager:
    """分配进程 ID，并让 exec_command 与 write_stdin 共享进程状态。"""

    def __init__(self, max_lifetime_seconds: float) -> None:
        self._max_lifetime_seconds = max_lifetime_seconds
        self._entries: dict[int, _ProcessEntry] = {}
        self._next_process_id = 1
        self._closed = False

    async def exec_command(
        self,
        spawn: Callable[[], Awaitable[ProcessHandle]],
        yield_time_ms: int,
        *,
        read_only: bool = False,
    ) -> ProcessResult:
        if self._closed:
            raise RuntimeError("进程管理器已经关闭")
        if len(self._entries) >= MAX_PROCESSES:
            raise RuntimeError(f"后台进程数量已达到上限 {MAX_PROCESSES}")

        process = await spawn()
        process_id = self._next_process_id
        self._next_process_id += 1
        entry = _ProcessEntry(process_id, process, read_only)
        self._entries[process_id] = entry
        entry.reader_task = asyncio.create_task(
            self._read_output(entry), name=f"agent-process-reader-{process_id}"
        )
        entry.waiter_task = asyncio.create_task(
            self._wait_process(entry), name=f"agent-process-waiter-{process_id}"
        )
        entry.timeout_task = asyncio.create_task(
            self._enforce_timeout(entry), name=f"agent-process-timeout-{process_id}"
        )
        try:
            return await self._collect(entry, yield_time_ms)
        except asyncio.CancelledError:
            # 还没把 process_id 交给模型时，保留进程会让它永远无法被续接。
            await self._stop(entry)
            self._remove(entry)
            raise

    async def write_stdin(
        self,
        process_id: int,
        chars: str,
        yield_time_ms: int,
        *,
        require_read_only: bool = False,
    ) -> ProcessResult:
        entry = self._entries.get(process_id)
        if entry is None:
            raise ValueError(f"进程不存在或已经结束: {process_id}")
        if require_read_only and not entry.read_only:
            raise PermissionError("Plan Mode 不能续接可写或宿主进程")
        if chars and not entry.process_done:
            try:
                await entry.process.write(chars.encode("utf-8"))
            except (BrokenPipeError, ConnectionError):
                # 进程可能刚好在写入前退出；等待 reader/waiter 收尾后返回真实退出状态。
                pass
        return await self._collect(entry, yield_time_ms)

    def close(self) -> None:
        """同步关闭入口只发终止信号；异步拥有者应继续调用 aclose() 回收。"""

        if self._closed:
            return
        self._closed = True
        for entry in tuple(self._entries.values()):
            if not entry.process_done:
                entry.process.kill()

    async def aclose(self) -> None:
        self.close()
        entries = tuple(self._entries.values())
        if entries:
            await asyncio.gather(*(entry.settled.wait() for entry in entries))
        for entry in entries:
            self._remove(entry)

    async def _read_output(self, entry: _ProcessEntry) -> None:
        try:
            while chunk := await entry.process.read():
                entry.push_output(chunk)
        except Exception as exc:
            entry.error = f"读取进程输出失败: {exc}"
            if not entry.process_done:
                entry.process.kill()
        finally:
            entry.reader_done = True
            entry.mark_settled()

    async def _wait_process(self, entry: _ProcessEntry) -> None:
        try:
            entry.exit_code = await entry.process.wait()
        except Exception as exc:
            entry.error = entry.error or f"等待进程退出失败: {exc}"
        finally:
            entry.process_done = True
            entry.mark_settled()

    async def _enforce_timeout(self, entry: _ProcessEntry) -> None:
        try:
            await asyncio.sleep(self._max_lifetime_seconds)
            if not entry.process_done:
                entry.error = f"命令超过 {self._max_lifetime_seconds:g} 秒，已终止"
                entry.process.kill()
        except asyncio.CancelledError:
            pass

    async def _collect(
        self, entry: _ProcessEntry, yield_time_ms: int
    ) -> ProcessResult:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + yield_time_ms / 1000
        post_exit_deadline: float | None = None
        collected = bytearray()
        omitted = 0
        try:
            while True:
                # 清除通知和 drain 之间不能 await；这样新输出不会落在“已检查但未等待”的缝隙里。
                entry.state_changed.clear()
                output, newly_omitted = entry.take_output()
                omitted += newly_omitted
                omitted = _append_bounded(collected, output, omitted)

                if entry.settled.is_set():
                    finished = True
                    break

                now = loop.time()
                if entry.process_done and post_exit_deadline is None:
                    # 进程退出和 pipe EOF 不是同一事件；留一个很短的窗口收齐最后输出。
                    post_exit_deadline = now + POST_EXIT_CLOSE_WAIT_SECONDS
                wait_until = deadline
                if post_exit_deadline is not None:
                    wait_until = min(wait_until, post_exit_deadline)
                remaining = wait_until - now
                if remaining <= 0:
                    finished = entry.process_done
                    break

                try:
                    await asyncio.wait_for(entry.state_changed.wait(), remaining)
                except TimeoutError:
                    finished = entry.process_done
                    break
        except asyncio.CancelledError:
            # write_stdin 可能被调用方取消；把已 drain 的内容放回，避免下一轮永久丢失输出。
            pending, pending_omitted = entry.take_output()
            entry.output = collected
            entry.omitted_bytes = omitted + pending_omitted
            entry.push_output(pending)
            raise

        # 超时和最后一个输出通知可能同时发生，返回前再无等待地 drain 一次。
        output, newly_omitted = entry.take_output()
        omitted += newly_omitted
        omitted = _append_bounded(collected, output, omitted)
        if finished and not entry.reader_done and entry.reader_task is not None:
            entry.reader_task.cancel()
            await asyncio.gather(entry.reader_task, return_exceptions=True)
        result = ProcessResult(
            _render_output(collected, omitted).decode("utf-8", errors="replace"),
            None if finished else entry.process_id,
            entry.exit_code if finished else None,
            omitted,
            entry.error,
        )
        if finished:
            self._remove(entry)
        return result

    async def _stop(self, entry: _ProcessEntry) -> None:
        if not entry.process_done:
            entry.process.kill()
        await entry.settled.wait()

    def _remove(self, entry: _ProcessEntry) -> None:
        if self._entries.get(entry.process_id) is entry:
            del self._entries[entry.process_id]
        if entry.timeout_task is not None and not entry.timeout_task.done():
            entry.timeout_task.cancel()


def validate_yield_time(value: object, default: int) -> int:
    resolved = default if value is None else value
    if not isinstance(resolved, int) or isinstance(resolved, bool):
        raise ValueError("yield_time_ms 必须是整数")
    if not MIN_YIELD_TIME_MS <= resolved <= MAX_YIELD_TIME_MS:
        raise ValueError(
            f"yield_time_ms 必须在 {MIN_YIELD_TIME_MS} 到 {MAX_YIELD_TIME_MS} 之间"
        )
    return resolved


def _append_bounded(buffer: bytearray, chunk: bytes, omitted: int) -> int:
    """保留完整输出的头尾，让首个根因和最终状态都能被模型看到。"""

    buffer.extend(chunk)
    overflow = len(buffer) - MAX_OUTPUT_BYTES
    if overflow <= 0:
        return omitted

    # 中间内容诊断价值最低；固定对半保留也让跨多次 drain 的结果保持稳定。
    head = bytes(buffer[:HEAD_OUTPUT_BYTES])
    tail = bytes(buffer[-TAIL_OUTPUT_BYTES:])
    buffer.clear()
    buffer.extend(head)
    buffer.extend(tail)
    return omitted + overflow


def _render_output(buffer: bytearray, omitted: int) -> bytes:
    if not omitted:
        return bytes(buffer)
    marker = f"\n... {omitted} bytes omitted ...\n".encode("ascii")
    return bytes(buffer[:HEAD_OUTPUT_BYTES]) + marker + bytes(buffer[HEAD_OUTPUT_BYTES:])
