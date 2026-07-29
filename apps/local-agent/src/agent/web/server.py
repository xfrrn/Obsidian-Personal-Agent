"""供 Obsidian 原生侧栏调用的本机 HTTP/SSE 后端。"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import ipaddress
import json
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath, PureWindowsPath
from tempfile import TemporaryDirectory
from threading import Lock, Thread
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from agent.changes import ChangeJournal
from agent.config.settings import Settings
from agent.core.handle import AgentHandle
from agent.core.loop import start_agent
from agent.core.turn.events import TurnError, TurnEvent, TurnFinished, TurnInterrupted
from agent.core.turn.public_events import PublicEventAdapter
from agent.memory import read_editable_memory, save_memory_override
from agent.permissions import ApprovalPolicy, SandboxMode
from agent.sandbox import SandboxBackend
from agent.skills.loader import discover_skills, is_valid_skill_name
from agent.utils.logging import configure_logging
from agent.web.metrics import AgentMetrics
from agent.protocol.event import Event, EventKind
from agent.protocol.mode import ModeKind
from agent.protocol.op import FileReference, Interrupt, ResolveApproval, UserInput
from agent.storage import SessionStore, StoredSession


_LOGGER = logging.getLogger(__name__)
MAX_FILE_REFERENCES = 5
MAX_WORKSPACE_FILE_RESULTS = 80
SKIPPED_WORKSPACE_DIRS = {".git", ".obsidian", ".pytest_cache", "__pycache__", "node_modules"}
OBSIDIAN_APP_ORIGIN = "app://obsidian.md"
MAX_SKILL_IMPORT_FILES = 500
MAX_SKILL_IMPORT_BYTES = 10 * 1024 * 1024
MAX_SKILL_IMPORT_BODY_BYTES = 16 * 1024 * 1024


@dataclass(slots=True)
class LiveSession:
    handle: AgentHandle
    runner: asyncio.Task[None]
    generation: int
    active_submission_id: int | None = None


class AgentRuntime:
    """将多个持久化 Agent 会话安全地托管到同步 HTTP 服务器中。

    ``http.server`` 的请求运行在不同线程；提交动作须与创建/关闭会话串行，
    但 SSE 消费按 submission 分流，不能阻塞后续用户输入。
    """

    def __init__(
        self, settings: Settings, client_factory: Callable[[], object] | None = None
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._store = SessionStore(settings.session_db_path)
        self._changes = ChangeJournal(settings.workspace, settings.session_db_path)
        self._sessions: dict[str, LiveSession] = {}
        self._next_generation = 0
        self._default_session_id: str | None = None
        self._turn_events: dict[tuple[str, int, int], asyncio.Queue[Event]] = {}
        self._metrics = AgentMetrics()
        # ponytail: 短临界区共用一把锁；出现可测量的请求竞争后再拆成每会话锁。
        self._request_lock = Lock()
        self._closed = False
        self._loop = asyncio.new_event_loop()
        self._thread = Thread(target=self._run_loop, name="agent-web-asyncio", daemon=True)
        self._thread.start()

    def new_conversation(self) -> str:
        """创建并返回一个不会影响其他活跃对话的持久化会话。"""

        with self._request_lock:
            session_id = self._call(self._new_conversation())
            self._default_session_id = session_id
            return session_id

    def conversations(self) -> list[dict[str, Any]]:
        return [_session_json(session) for session in self._store.list()]

    def permissions(self) -> dict[str, Any]:
        """返回当前进程实际使用的权限，而不是重新读取可能已变化的环境变量。"""

        return {
            "sandbox_mode": self._settings.sandbox_mode.value,
            "approval_policy": self._settings.approval_policy.value,
            "shell_enabled": self._settings.shell_enabled,
            "disabled_skills": sorted(self._settings.disabled_skills),
            "sandbox_backend": self._settings.sandbox_backend.value,
            "sandbox_network": self._settings.sandbox_network.value,
        }

    def configuration(self) -> dict[str, Any]:
        """返回可由插件持久化的配置，绝不回传密钥正文。"""

        return {
            "api_key_configured": bool(self._settings.api_key),
            "base_url": self._settings.base_url,
            "model": self._settings.model,
            "workspace": str(self._settings.workspace),
            "sandbox_mode": self._settings.sandbox_mode.value,
            "approval_policy": self._settings.approval_policy.value,
            "shell_enabled": self._settings.shell_enabled,
            "disabled_skills": sorted(self._settings.disabled_skills),
            "session_db_path": str(self._settings.session_db_path),
        }

    def workspace_files(self, query: str = "") -> dict[str, Any]:
        workspace = self._settings.workspace.resolve()
        needle = query.strip().lower()[:200]
        files: list[dict[str, str]] = []
        for root, dirnames, filenames in os.walk(workspace):
            dirnames[:] = sorted(
                name for name in dirnames if not _skip_workspace_entry(name)
            )
            for filename in sorted(filenames):
                if _skip_workspace_entry(filename):
                    continue
                path = Path(root) / filename
                try:
                    relative = path.resolve().relative_to(workspace)
                except (OSError, ValueError):
                    continue
                display_path = relative.as_posix()
                if needle and needle not in display_path.lower():
                    continue
                files.append({"path": display_path})
                if len(files) >= MAX_WORKSPACE_FILE_RESULTS:
                    return {"files": files}
        return {"files": files}

    def skills(self) -> dict[str, Any]:
        """返回当前工作区中已通过格式校验的 Skills。"""

        return {
            "skills": [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "enabled": skill.name not in self._settings.disabled_skills,
                }
                for skill in discover_skills(self._settings.skills_dir)
            ]
        }

    def memory(self) -> dict[str, str]:
        """返回用户可编辑且 Agent 实际优先使用的长期记忆。"""

        return {"memory": read_editable_memory(self._settings.memory_dir)}

    def update_memory(self, content: str) -> dict[str, str]:
        """保存用户维护的长期记忆；自动合并文件继续独立更新。"""

        try:
            saved = save_memory_override(self._settings.memory_dir, content)
        except OSError as exc:
            raise ValueError(f"无法保存长期记忆: {exc}") from exc
        return {"memory": saved}

    def import_skill(self, files: dict[PurePosixPath, bytes]) -> dict[str, Any]:
        """校验并原子导入一个 Skill 目录，不覆盖已有同名 Skill。"""

        with self._request_lock:
            skills_root = self._settings.skills_dir
            if skills_root.is_symlink():
                raise ValueError("skills 目录不能是符号链接")
            try:
                skills_root.parent.mkdir(parents=True, exist_ok=True)
                with TemporaryDirectory(
                    prefix=".skill-import-", dir=skills_root.parent
                ) as directory:
                    staging_root = Path(directory)
                    for relative, content in files.items():
                        target = staging_root.joinpath(*relative.parts)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(content)
                    skills = discover_skills(staging_root)
                    if len(skills) != 1:
                        raise ValueError("选中的目录根部必须包含一个有效的 SKILL.md")
                    skill = skills[0]
                    destination = skills_root / skill.name
                    if destination.exists() or destination.is_symlink():
                        raise RuntimeError(f"Skill 已存在: {skill.name}")
                    skills_root.mkdir(parents=True, exist_ok=True)
                    skill.path.parent.replace(destination)
            except OSError as exc:
                raise ValueError(f"无法写入 Skill: {exc}") from exc
        return {
            "skill": {
                "name": skill.name,
                "description": skill.description,
                "enabled": skill.name not in self._settings.disabled_skills,
            }
        }

    def update_configuration(self, settings: Settings) -> dict[str, Any]:
        """在空闲边界替换冻结配置，后续会话使用新的模型、工具和工作区。"""

        with self._request_lock:
            if self._turn_events or self._metrics.snapshot()["active_turns"]:
                raise RuntimeError("运行中的回合结束后才能应用配置")
            self._call(self._shutdown())
            self._settings = settings
            self._store = SessionStore(settings.session_db_path)
            self._changes = ChangeJournal(settings.workspace, settings.session_db_path)
            self._default_session_id = None
            return self.configuration()

    def update_permissions(self, sandbox_mode: SandboxMode) -> dict[str, Any]:
        """在空闲时替换全局权限，并重建已加载会话的冻结工具配置。"""

        with self._request_lock:
            if sandbox_mode is self._settings.sandbox_mode:
                return self.permissions()
            if self._turn_events or self._metrics.snapshot()["active_turns"]:
                raise RuntimeError("运行中的回合结束后才能切换权限")
            if (
                self._settings.shell_enabled
                and sandbox_mode is not SandboxMode.DANGER_FULL_ACCESS
                and self._settings.sandbox_backend is not SandboxBackend.DISABLED
                and self._settings.sandbox_state_dir.is_relative_to(
                    self._settings.workspace
                )
            ):
                # danger-full-access 启动时允许此配置；降权后必须重新满足沙盒状态目录边界。
                raise ValueError("AGENT_SANDBOX_STATE 必须位于模型可写工作区之外")
            self._call(self._shutdown())
            self._settings = replace(self._settings, sandbox_mode=sandbox_mode)
            return self.permissions()

    def conversation(self, session_id: str) -> dict[str, Any]:
        stored = self._store.load(session_id)
        if stored is None:
            raise KeyError(f"会话不存在或已归档: {session_id}")
        return {
            "session": _session_json(stored),
            "messages": [
                {
                    "id": f"{stored.id}:{message.seq}",
                    "role": message.role,
                    "text": message.payload.get(
                        "display_content", message.payload.get("content", "")
                    ),
                    "references": _message_references(message.payload),
                    "created_at": message.created_at,
                }
                for message in stored.messages
                if message.visible
                and message.role in {"user", "assistant"}
                and isinstance(message.payload.get("content"), str)
            ],
            "changes": self._changes.list_session(session_id),
        }

    def change_detail(self, session_id: str, change_set_id: str) -> dict[str, object]:
        if self._store.load(session_id) is None:
            raise KeyError(f"会话不存在或已归档: {session_id}")
        return self._changes.get(session_id, change_set_id)

    def undo_changes(
        self, session_id: str, change_set_id: str, paths: list[str]
    ) -> dict[str, object]:
        with self._request_lock:
            if self._metrics.snapshot()["active_turns"]:
                raise RuntimeError("运行中的回合结束后才能撤销文件")
            if self._store.load(session_id) is None:
                raise KeyError(f"会话不存在或已归档: {session_id}")
            return self._changes.undo(session_id, change_set_id, paths)

    def archive_conversation(self, session_id: str) -> None:
        with self._request_lock:
            self._call(self._archive_conversation(session_id))
            if self._default_session_id == session_id:
                self._default_session_id = None

    def interrupt(self, session_id: str) -> bool:
        """只中断调用时仍活跃的目标回合，避免迟到请求误伤下一轮。"""

        with self._request_lock:
            return self._call(self._interrupt(session_id))

    def ask(
        self,
        text: str,
        session_id: str | None = None,
        mode: ModeKind | None = None,
        references: tuple[str, ...] = (),
    ) -> list[Event]:
        """提交一条消息，并收集该回合的全部可展示事件。"""

        return list(self.ask_events(text, session_id, mode, references))

    def ask_events(
        self,
        text: str,
        session_id: str | None = None,
        mode: ModeKind | None = None,
        references: tuple[str, ...] = (),
    ) -> Iterator[Event]:
        """逐条产出回合事件，供 SSE 在模型生成期间立即转发。"""

        file_references = self._resolve_file_references(references)
        if not text.strip() and not file_references:
            raise ValueError("消息不能为空")
        if len(text) > 20_000:
            raise ValueError("消息不能超过 20000 个字符")
        return self._ask_events(text, session_id, mode, file_references)

    def _ask_events(
        self,
        text: str,
        session_id: str | None,
        mode: ModeKind | None,
        references: tuple[FileReference, ...],
    ) -> Iterator[Event]:
        # 只保护提交动作。持锁等完整 SSE 会使下一条输入无法到达调度器，
        # 从而把可取消的 steering 又变回串行对话。
        with self._request_lock:
            resolved_session_id = session_id or self._default_session_id
            if resolved_session_id is None:
                raise RuntimeError("请先创建或选择会话")
            turn_key = self._call(
                self._submit(resolved_session_id, text, mode, references)
            )
        try:
            while True:
                event = self._call(self._next_turn_event(turn_key))
                yield event
                if event.kind in {
                    EventKind.TURN_FINISHED,
                    EventKind.TURN_INTERRUPTED,
                    EventKind.ERROR,
                    EventKind.SHUTDOWN,
                }:
                    return
        finally:
            # A disconnected browser must not keep accumulating stream deltas. The
            # turn itself remains active until later steering or shutdown cancels it.
            if not self._closed:
                with self._request_lock:
                    if not self._closed:
                        self._call(self._discard_turn_events(turn_key))

    def _resolve_file_references(self, paths: tuple[str, ...]) -> tuple[FileReference, ...]:
        if len(paths) > MAX_FILE_REFERENCES:
            raise ValueError(f"references 不能超过 {MAX_FILE_REFERENCES} 个文件")
        workspace = self._settings.workspace.resolve()
        references: list[FileReference] = []
        seen: set[str] = set()
        for raw_path in paths:
            if not isinstance(raw_path, str) or not raw_path.strip() or len(raw_path) > 2_000:
                raise ValueError("reference path 必须是有效的工作区相对路径")
            path_text = raw_path.strip().replace("\\", "/")
            if Path(path_text).is_absolute():
                raise ValueError("@ 引用只能使用工作区内的相对路径")
            target = (workspace / path_text).resolve()
            try:
                relative = target.relative_to(workspace)
            except ValueError as exc:
                raise ValueError("@ 引用不能指向工作区外的文件") from exc
            display_path = relative.as_posix()
            if display_path in seen:
                continue
            if not target.is_file():
                raise ValueError(f"@ 引用文件不存在: {display_path}")
            references.append(FileReference(display_path))
            seen.add(display_path)
        return tuple(references)

    def resolve_approval(
        self,
        call_id: str,
        approved: bool,
        submission_id: int,
        session_id: str | None = None,
    ) -> bool:
        """把浏览器答复作为控制操作排入原 Session；核心运行时负责拒绝迟到答复。"""

        if not call_id or len(call_id) > 256:
            raise ValueError("call_id 无效")
        if submission_id <= 0:
            raise ValueError("submission_id 必须为正整数")
        with self._request_lock:
            resolved_session_id = session_id or self._default_session_id
            if resolved_session_id is None:
                raise RuntimeError("请先创建或选择会话")
            return self._call(
                self._resolve_approval(
                    resolved_session_id, call_id, approved, submission_id
                )
            )

    def close(self) -> None:
        """有序停止 Agent 后关闭后台事件循环，避免解释器退出时遗留任务。"""

        with self._request_lock:
            if self._closed:
                return
            self._call(self._shutdown())
            self._closed = True
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()

    def _call(self, coroutine: Any) -> Any:
        if self._closed:
            raise RuntimeError("服务已关闭")
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
        self._loop.close()

    async def _new_conversation(self) -> str:
        stored = await asyncio.to_thread(
            self._store.create, self._settings.model, self._settings.workspace
        )
        await self._ensure_live(stored.id)
        return stored.id

    async def _ensure_live(self, session_id: str) -> LiveSession:
        live = self._sessions.get(session_id)
        if live is not None:
            return live

        self._next_generation += 1
        generation = self._next_generation
        client = self._client_factory() if self._client_factory else None
        handle, runner = await start_agent(
            self._settings,
            client,
            session_id=session_id,
            store=self._store,
            change_journal=self._changes,
        )
        live = LiveSession(handle, runner, generation)
        self._sessions[session_id] = live
        # generation 防止同一持久化会话重新载入后，旧 SSE 收到相同 submission id 的事件。
        handle.turn_events.subscribe(
            lambda event: self._route_turn_event(session_id, generation, event),
            include_deltas=True,
        )
        # 复用可靠事件总线，指标读取不会影响回合调度或 SSE 消费。
        handle.turn_events.subscribe(
            lambda event: self._metrics.record(event, session_id)
        )
        # 在接受用户输入前订阅，保证可靠事件完整进入入口流，并尽早开始接收流式增量。
        return live

    def _route_turn_event(self, session_id: str, generation: int, event: TurnEvent) -> None:
        """按 submission 隔离事件，让被取消和新启动的 SSE 能并存。"""

        submission_id = getattr(event, "submission_id", None)
        change_set = None
        if isinstance(event, (TurnFinished, TurnInterrupted, TurnError)) and isinstance(
            submission_id, int
        ):
            try:
                change_set = self._changes.finish_turn(session_id, submission_id)
            except Exception:
                _LOGGER.exception("change_journal.finish_failed")
        public_event = PublicEventAdapter.adapt(event)
        if public_event is None or not isinstance(submission_id, int):
            return
        queue = self._turn_events.get((session_id, generation, submission_id))
        if queue is not None:
            if change_set is not None:
                queue.put_nowait(
                    Event(EventKind.FILE_CHANGES, "文件修改已记录。", change_set)
                )
            queue.put_nowait(public_event)
        live = self._sessions.get(session_id)
        if (
            live is not None
            and live.generation == generation
            and live.active_submission_id == submission_id
            and public_event.kind
            in {EventKind.TURN_FINISHED, EventKind.TURN_INTERRUPTED, EventKind.ERROR}
        ):
            live.active_submission_id = None

    async def _next_turn_event(self, turn_key: tuple[str, int, int]) -> Event:
        queue = self._turn_events.get(turn_key)
        if queue is None:
            raise RuntimeError("会话已关闭")
        event = await queue.get()
        if event.kind in {
            EventKind.TURN_FINISHED,
            EventKind.TURN_INTERRUPTED,
            EventKind.ERROR,
            EventKind.SHUTDOWN,
        }:
            self._turn_events.pop(turn_key, None)
        return event

    async def _discard_turn_events(self, turn_key: tuple[str, int, int]) -> None:
        self._turn_events.pop(turn_key, None)

    async def _submit(
        self,
        session_id: str,
        text: str,
        mode: ModeKind | None,
        references: tuple[FileReference, ...],
    ) -> tuple[str, int, int]:
        live = await self._ensure_live(session_id)
        submission_id = live.handle.submit(UserInput(text, mode, references))
        live.active_submission_id = submission_id
        turn_key = (session_id, live.generation, submission_id)
        # submit() 不会让出事件循环，因此调度器还没来得及发 TurnStarted；
        # 在这里建队列可确保新 turn 的首个事件不会丢失。
        self._turn_events[turn_key] = asyncio.Queue()
        return turn_key

    async def _interrupt(self, session_id: str) -> bool:
        live = self._sessions.get(session_id)
        if live is None or live.runner.done() or live.active_submission_id is None:
            return False
        live.handle.submit(Interrupt(live.active_submission_id))
        return True

    async def _resolve_approval(
        self, session_id: str, call_id: str, approved: bool, submission_id: int
    ) -> bool:
        live = self._sessions.get(session_id)
        if live is None or live.runner.done():
            return False
        live.handle.submit(ResolveApproval(call_id, approved, submission_id))
        return True

    async def _archive_conversation(self, session_id: str) -> None:
        await self._shutdown_live(session_id)
        self._turn_events = {
            key: queue for key, queue in self._turn_events.items() if key[0] != session_id
        }
        await asyncio.to_thread(self._store.archive, session_id)

    async def _shutdown_live(self, session_id: str) -> None:
        live = self._sessions.pop(session_id, None)
        if live is None:
            return
        if not live.runner.done():
            live.handle.shutdown()
        await live.runner
        self._changes.finish_active_session(session_id)

    async def _shutdown(self) -> None:
        live_sessions = tuple(self._sessions.items())
        for _, live in live_sessions:
            if not live.runner.done():
                live.handle.shutdown()
        if live_sessions:
            await asyncio.gather(*(live.runner for _, live in live_sessions))
            for session_id, _ in live_sessions:
                self._changes.finish_active_session(session_id)
        self._sessions.clear()

    def metrics(self) -> dict[str, Any]:
        """返回当前 Python 进程内跨会话累计的安全监控数据。"""

        return self._metrics.snapshot()


class AgentHTTPServer(ThreadingHTTPServer):
    """把共享运行时放在服务器实例上，避免使用模块级全局变量。"""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], runtime: AgentRuntime) -> None:
        super().__init__(address, AgentRequestHandler)
        self.runtime = runtime


class AgentRequestHandler(BaseHTTPRequestHandler):
    """提供持久化会话 API 和按会话隔离的消息流。"""

    server: AgentHTTPServer

    def log_message(self, format: str, *args: object) -> None:
        """Keep http.server's access output inside the configured Agent logger.

        The base implementation logs the raw request line, which can include a query
        string. Only the method and path are useful for local diagnostics.
        """

        _LOGGER.debug(
            "http.request",
            extra={"http_method": self.command, "http_path": urlparse(self.path).path},
        )

    def end_headers(self) -> None:
        """仅向 Obsidian 桌面宿主开放跨源响应，普通本机客户端不受影响。"""

        if self.headers.get("Origin") == OBSIDIAN_APP_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", OBSIDIAN_APP_ORIGIN)
            self.send_header("Vary", "Origin")
        super().end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的固定方法名。
        """处理 Obsidian 对 JSON POST 发起的 CORS 预检。"""

        if self.headers.get("Origin") != OBSIDIAN_APP_ORIGIN:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "不允许的请求来源"})
            return
        requested_method = self.headers.get("Access-Control-Request-Method", "")
        if requested_method not in {"GET", "POST"}:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "不允许的请求方法"})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的固定方法名。
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/config":
            if not _is_loopback_client(self.client_address[0]):
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "配置只允许本机读取"})
                return
            self._send_json(HTTPStatus.OK, self.server.runtime.configuration())
            return
        if path == "/api/metrics":
            self._send_json(HTTPStatus.OK, self.server.runtime.metrics())
            return
        if path == "/api/permissions":
            self._send_json(HTTPStatus.OK, self.server.runtime.permissions())
            return
        if path == "/api/workspace/files":
            query = parse_qs(parsed.query).get("q", [""])[0]
            self._send_json(HTTPStatus.OK, self.server.runtime.workspace_files(query))
            return
        if path == "/api/memory":
            if not _is_loopback_client(self.client_address[0]):
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "长期记忆只允许本机读取"})
                return
            try:
                self._send_json(HTTPStatus.OK, self.server.runtime.memory())
            except (OSError, ValueError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if path == "/api/skills":
            if not _is_loopback_client(self.client_address[0]):
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Skills 只允许本机读取"})
                return
            try:
                self._send_json(HTTPStatus.OK, self.server.runtime.skills())
            except (OSError, ValueError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if path == "/api/sessions":
            self._send_json(
                HTTPStatus.OK, {"sessions": self.server.runtime.conversations()}
            )
            return
        route = _session_route(path)
        change_route = _change_route(route[1]) if route is not None else None
        if route is not None and change_route is not None and change_route[1] == "":
            try:
                self._send_json(
                    HTTPStatus.OK,
                    self.server.runtime.change_detail(route[0], change_route[0]),
                )
            except KeyError as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            return
        if route is not None and route[1] == "":
            try:
                self._send_json(
                    HTTPStatus.OK, self.server.runtime.conversation(route[0])
                )
            except KeyError as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "未找到接口"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的固定方法名。
        try:
            path = urlparse(self.path).path
            if path == "/api/config":
                if not _is_loopback_client(self.client_address[0]):
                    self._send_json(HTTPStatus.FORBIDDEN, {"error": "配置只允许从本机修改"})
                    return
                settings = _configuration_body(
                    self._read_json_body(), self.server.runtime._settings
                )
                self._send_json(
                    HTTPStatus.OK,
                    self.server.runtime.update_configuration(settings),
                )
                return
            if path == "/api/memory":
                if not _is_loopback_client(self.client_address[0]):
                    self._send_json(HTTPStatus.FORBIDDEN, {"error": "长期记忆只允许从本机修改"})
                    return
                body = self._read_json_body(50_100)
                if set(body) != {"memory"} or not isinstance(body["memory"], str):
                    raise ValueError("长期记忆请求必须且只能包含 memory 字符串")
                self._send_json(
                    HTTPStatus.OK,
                    self.server.runtime.update_memory(body["memory"]),
                )
                return
            if path == "/api/permissions":
                if not _is_loopback_client(self.client_address[0]):
                    self._send_json(
                        HTTPStatus.FORBIDDEN,
                        {"error": "权限切换只允许来自本机的请求"},
                    )
                    return
                sandbox_mode = _permissions_body(self._read_json_body())
                self._send_json(
                    HTTPStatus.OK,
                    self.server.runtime.update_permissions(sandbox_mode),
                )
                return
            if path == "/api/skills/import":
                if not _is_loopback_client(self.client_address[0]):
                    self._send_json(HTTPStatus.FORBIDDEN, {"error": "Skill 只允许从本机导入"})
                    return
                files = _skill_import_files(
                    self._read_json_body(MAX_SKILL_IMPORT_BODY_BYTES)
                )
                self._send_json(
                    HTTPStatus.CREATED, self.server.runtime.import_skill(files)
                )
                return
            if path == "/api/sessions":
                session_id = self.server.runtime.new_conversation()
                self._send_json(
                    HTTPStatus.CREATED,
                    self.server.runtime.conversation(session_id)["session"],
                )
                return
            route = _session_route(path)
            change_route = _change_route(route[1]) if route is not None else None
            if route is not None and change_route is not None and change_route[1] == "undo":
                paths = _undo_paths(self._read_json_body())
                self._send_json(
                    HTTPStatus.OK,
                    self.server.runtime.undo_changes(route[0], change_route[0], paths),
                )
                return
            if route is not None and route[1] == "approvals":
                body = self._read_json_body()
                call_id, approved, submission_id = _approval_body(body)
                if not self.server.runtime.resolve_approval(
                    call_id, approved, submission_id, route[0]
                ):
                    raise RuntimeError("审批请求已失效")
                self._send_json(HTTPStatus.OK, {"ok": True})
                return
            if route is not None and route[1] == "interrupt":
                if not self.server.runtime.interrupt(route[0]):
                    raise RuntimeError("当前没有运行中的回合")
                self._send_json(HTTPStatus.OK, {"ok": True})
                return
            if route is not None and route[1] in {"messages", "messages/stream"}:
                body = self._read_json_body()
                text = body.get("text")
                if not isinstance(text, str):
                    raise ValueError("text 必须是字符串")
                mode = _mode_body(body)
                references = _reference_paths(body)
                if route[1] == "messages/stream":
                    self._stream_events(
                        self.server.runtime.ask_events(text, route[0], mode, references)
                    )
                else:
                    events = self.server.runtime.ask(text, route[0], mode, references)
                    self._send_json(
                        HTTPStatus.OK,
                        {"events": [_event_json(event) for event in events]},
                    )
                return
            if route is not None and route[1] == "archive":
                self.server.runtime.archive_conversation(route[0])
                self._send_json(HTTPStatus.OK, {"ok": True})
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "未找到接口"})
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except KeyError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except RuntimeError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})

    def _read_json_body(self, max_bytes: int = 80_100) -> dict[str, Any]:
        """在读取前限制请求体，避免本地服务被意外的大粘贴内容耗尽内存。"""

        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError as exc:
            raise ValueError("Content-Length 无效") from exc
        if not 0 <= length <= max_bytes:
            raise ValueError(f"请求体不能超过 {max_bytes} 字节")
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return body

    def _send_json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _stream_events(self, events: Iterator[Event]) -> None:
        """将已有 Event 协议以 SSE 发送，避免 Web 端等待整个回合结束。"""

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for event in events:
                payload = json.dumps(_event_json(event), ensure_ascii=False).encode("utf-8")
                self.wfile.write(b"data: " + payload + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # 浏览器已离开页面，停止继续向已关闭的连接写入；后续 steering 或关闭仍可取消回合。
            return
        finally:
            close = getattr(events, "close", None)
            if callable(close):
                close()


def _event_json(event: Event) -> dict[str, Any]:
    """Event 是内部 dataclass；此处才转换为稳定、浏览器可消费的 JSON。"""

    return {"kind": event.kind.value, "text": event.text, "data": event.data}


def _approval_body(body: dict[str, Any]) -> tuple[str, bool, int]:
    call_id = body.get("call_id")
    approved = body.get("approved")
    submission_id = body.get("submission_id")
    if not isinstance(call_id, str):
        raise ValueError("call_id 必须是字符串")
    if not isinstance(approved, bool):
        raise ValueError("approved 必须是布尔值")
    if not isinstance(submission_id, int) or isinstance(submission_id, bool):
        raise ValueError("submission_id 必须是整数")
    return call_id, approved, submission_id


def _mode_body(body: dict[str, Any]) -> ModeKind | None:
    value = body.get("mode")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("mode 必须是字符串")
    return ModeKind.parse(value)


def _reference_paths(body: dict[str, Any]) -> tuple[str, ...]:
    if "references" not in body or body.get("references") is None:
        return ()
    raw_references = body["references"]
    if not isinstance(raw_references, list):
        raise ValueError("references 必须是数组")
    paths: list[str] = []
    for item in raw_references:
        if isinstance(item, str):
            paths.append(item)
        elif isinstance(item, dict) and isinstance(item.get("path"), str):
            paths.append(item["path"])
        else:
            raise ValueError("references 只能包含文件路径")
    return tuple(paths)


def _undo_paths(body: dict[str, Any]) -> list[str]:
    if set(body) != {"paths"} or not isinstance(body["paths"], list):
        raise ValueError("撤销请求必须且只能包含 paths 数组")
    paths = body["paths"]
    if not 1 <= len(paths) <= 500 or any(
        not isinstance(path, str) or not path or len(path) > 2_000 for path in paths
    ):
        raise ValueError("paths 必须包含 1 到 500 个有效文件路径")
    return paths


def _skill_import_files(body: dict[str, Any]) -> dict[PurePosixPath, bytes]:
    """校验浏览器目录选择结果，并解码为安全的相对路径文件集。"""

    if set(body) != {"files"} or not isinstance(body["files"], list):
        raise ValueError("Skill 导入请求必须且只能包含 files 数组")
    entries = body["files"]
    if not 1 <= len(entries) <= MAX_SKILL_IMPORT_FILES:
        raise ValueError(f"Skill 必须包含 1 到 {MAX_SKILL_IMPORT_FILES} 个文件")

    files: dict[PurePosixPath, bytes] = {}
    roots: set[str] = set()
    total_bytes = 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "content"}:
            raise ValueError("每个 Skill 文件必须且只能包含 path 和 content")
        raw_path, encoded = entry["path"], entry["content"]
        if not isinstance(raw_path, str) or not isinstance(encoded, str):
            raise ValueError("Skill 文件的 path 和 content 必须是字符串")
        relative = PurePosixPath(raw_path)
        if (
            not raw_path
            or len(raw_path) > 2_000
            or "\\" in raw_path
            or relative.is_absolute()
            or bool(PureWindowsPath(raw_path).drive)
            or ".." in relative.parts
            or len(relative.parts) < 2
        ):
            raise ValueError(f"Skill 文件路径无效: {raw_path}")
        if relative in files:
            raise ValueError(f"Skill 包含重复文件: {raw_path}")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"Skill 文件不是有效的 Base64: {raw_path}") from exc
        total_bytes += len(content)
        if total_bytes > MAX_SKILL_IMPORT_BYTES:
            raise ValueError("Skill 文件总大小不能超过 10 MiB")
        files[relative] = content
        roots.add(relative.parts[0])

    if len(roots) != 1:
        raise ValueError("一次只能导入一个 Skill 目录")
    root = next(iter(roots))
    if PurePosixPath(root, "SKILL.md") not in files:
        raise ValueError("选中的目录根部缺少 SKILL.md")
    return files


def _permissions_body(body: dict[str, Any]) -> SandboxMode:
    if set(body) - {"sandbox_mode", "confirmed"}:
        raise ValueError("权限请求只能包含 sandbox_mode 和 confirmed")
    value = body.get("sandbox_mode")
    if not isinstance(value, str):
        raise ValueError("sandbox_mode 必须是字符串")
    sandbox_mode = SandboxMode.parse(value)
    if (
        sandbox_mode is SandboxMode.DANGER_FULL_ACCESS
        and body.get("confirmed") is not True
    ):
        raise ValueError("切换到 danger-full-access 必须显式确认")
    return sandbox_mode


def _configuration_body(body: dict[str, Any], current: Settings) -> Settings:
    allowed = {
        "api_key",
        "base_url",
        "model",
        "workspace",
        "sandbox_mode",
        "approval_policy",
        "shell_enabled",
        "disabled_skills",
        "session_db_path",
        "confirmed",
    }
    if unknown := set(body) - allowed:
        raise ValueError(f"未知配置字段: {', '.join(sorted(unknown))}")

    api_key = body.get("api_key", current.api_key)
    if api_key is not None and not isinstance(api_key, str):
        raise ValueError("api_key 必须是字符串")

    base_url = _required_config_text(body, "base_url", current.base_url)
    parsed_url = urlparse(base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise ValueError("base_url 必须是有效的 HTTP(S) 地址")
    if parsed_url.username or parsed_url.password:
        raise ValueError("base_url 不能包含用户名或密码")

    model = _required_config_text(body, "model", current.model)
    if len(model) > 200:
        raise ValueError("model 不能超过 200 个字符")

    workspace = Path(
        _required_config_text(body, "workspace", str(current.workspace))
    ).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace 不是目录: {workspace}")

    sandbox_mode = SandboxMode.parse(
        _required_config_text(body, "sandbox_mode", current.sandbox_mode.value)
    )
    if sandbox_mode is SandboxMode.DANGER_FULL_ACCESS and body.get("confirmed") is not True:
        raise ValueError("danger-full-access 必须显式确认")
    approval_policy = ApprovalPolicy.parse(
        _required_config_text(body, "approval_policy", current.approval_policy.value)
    )
    shell_enabled = body.get("shell_enabled", current.shell_enabled)
    if not isinstance(shell_enabled, bool):
        raise ValueError("shell_enabled 必须是布尔值")
    raw_disabled_skills = body.get(
        "disabled_skills", sorted(current.disabled_skills)
    )
    if not isinstance(raw_disabled_skills, list) or any(
        not isinstance(name, str) or not is_valid_skill_name(name)
        for name in raw_disabled_skills
    ):
        raise ValueError("disabled_skills 必须是有效 Skill 名称数组")
    disabled_skills = frozenset(raw_disabled_skills)

    raw_session_db = body.get("session_db_path")
    if raw_session_db is None or raw_session_db == "":
        session_db_path = current.session_db_path
    elif isinstance(raw_session_db, str):
        session_db_path = Path(raw_session_db).expanduser().resolve()
        if session_db_path.is_dir():
            raise ValueError("session_db_path 必须是文件路径")
    else:
        raise ValueError("session_db_path 必须是字符串")

    if (
        shell_enabled
        and sandbox_mode is not SandboxMode.DANGER_FULL_ACCESS
        and current.sandbox_backend is not SandboxBackend.DISABLED
        and current.sandbox_state_dir.is_relative_to(workspace)
    ):
        raise ValueError("沙盒状态目录必须位于工作区之外")

    return replace(
        current,
        api_key=api_key.strip() or None if isinstance(api_key, str) else None,
        base_url=base_url.rstrip("/"),
        model=model,
        workspace=workspace,
        sandbox_mode=sandbox_mode,
        approval_policy=approval_policy,
        shell_enabled=shell_enabled,
        disabled_skills=disabled_skills,
        session_db_path=session_db_path,
    )


def _required_config_text(body: dict[str, Any], key: str, fallback: str) -> str:
    value = body.get(key, fallback)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} 必须是非空字符串")
    return value.strip()


def _is_loopback_client(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or bool(
        address.version == 6
        and address.ipv4_mapped is not None
        and address.ipv4_mapped.is_loopback
    )


def _session_json(session: StoredSession) -> dict[str, Any]:
    return {
        "id": session.id,
        "title": session.title,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "model": session.model,
        "workspace": session.workspace,
        "last_turn_state": session.last_turn_state,
        "mode": session.mode.value,
        "plan": session.current_plan.as_dict() if session.current_plan is not None else None,
    }


def _message_references(payload: dict[str, Any]) -> list[dict[str, str]]:
    references = payload.get("references")
    if not isinstance(references, list):
        return []
    return [
        {"path": item["path"]}
        for item in references
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    ]


def _skip_workspace_entry(name: str) -> bool:
    return name in SKIPPED_WORKSPACE_DIRS or name.startswith(".")


def _session_route(path: str) -> tuple[str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) < 3 or parts[:2] != ["api", "sessions"]:
        return None
    session_id = parts[2]
    if not session_id or len(session_id) > 64:
        return None
    return session_id, "/".join(parts[3:])


def _change_route(action: str) -> tuple[str, str] | None:
    parts = action.split("/")
    if len(parts) not in {2, 3} or parts[0] != "changes":
        return None
    change_set_id = parts[1]
    trailing = parts[2] if len(parts) == 3 else ""
    if not change_set_id or len(change_set_id) > 64 or trailing not in {"", "undo"}:
        return None
    return change_set_id, trailing


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 Agent 本地聊天页面")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    parser.add_argument("--port", default=8000, type=int, help="监听端口")
    args = parser.parse_args()

    settings = Settings.from_env()
    configure_logging(settings.log_level, settings.log_format)
    runtime = AgentRuntime(settings)
    server = AgentHTTPServer((args.host, args.port), runtime)
    print(f"Agent 页面已启动：http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        runtime.close()


if __name__ == "__main__":
    main()
