"""SQLite-backed conversation persistence for local Agent sessions."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from agent.protocol.mode import ModeKind
from agent.tools.types import PlanUpdate


DEFAULT_SESSION_PAGE_SIZE = 100
MAX_SESSION_PAGE_SIZE = 500


@dataclass(frozen=True, slots=True)
class StoredMessage:
    seq: int
    role: str
    payload: dict[str, Any]
    created_at: int
    visible: bool


@dataclass(frozen=True, slots=True)
class StoredSession:
    id: str
    title: str
    created_at: int
    updated_at: int
    archived_at: int | None
    model: str
    workspace: str
    context_window_number: int
    context_summary: str | None
    active_from_seq: int
    last_turn_state: str
    mode: ModeKind
    current_plan: PlanUpdate | None
    messages: tuple[StoredMessage, ...] = ()

    @property
    def active_messages(self) -> list[dict[str, Any]]:
        """Return only the raw messages still belonging to the model context window."""

        return [dict(message.payload) for message in self.messages if message.seq >= self.active_from_seq]


@dataclass(frozen=True, slots=True)
class StoredMemory:
    """一次会话级记忆提取；source_updated_at 让同一会话后续追加内容时可重新提取。"""

    session_id: str
    workspace: str
    source_updated_at: int
    raw_memory: str
    rollout_summary: str
    created_at: int


class SessionStore:
    """Store session metadata and append-only model messages in one SQLite database."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create(self, model: str, workspace: Path) -> StoredSession:
        session_id = str(uuid.uuid4())
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    id, title, created_at, updated_at, model, workspace
                ) VALUES (?, '新对话', ?, ?, ?, ?)
                """,
                (session_id, now, now, model, str(workspace)),
            )
        stored = self.load(session_id)
        assert stored is not None
        return stored

    def list(
        self,
        limit: int = DEFAULT_SESSION_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[StoredSession, ...]:
        if type(limit) is not int or not 1 <= limit <= MAX_SESSION_PAGE_SIZE:
            raise ValueError(f"limit 必须是 1 到 {MAX_SESSION_PAGE_SIZE} 之间的整数")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset 必须是非负整数")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sessions
                WHERE archived_at IS NULL
                ORDER BY updated_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return tuple(_session_from_row(row) for row in rows)

    def load(self, session_id: str, *, include_archived: bool = False) -> StoredSession | None:
        with self._connect() as connection:
            session_row = connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if session_row is None or (session_row["archived_at"] is not None and not include_archived):
                return None
            message_rows = connection.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY seq", (session_id,)
            ).fetchall()
        return _session_from_row(
            session_row,
            tuple(_message_from_row(row) for row in message_rows),
        )

    def append_messages(
        self,
        session_id: str,
        submission_id: int | None,
        messages: Sequence[tuple[dict[str, Any], bool]],
        turn_state: str,
        plan_update: PlanUpdate | None = None,
    ) -> None:
        """Append completed model messages and update the checkpoint atomically."""

        if turn_state not in {"idle", "running", "interrupted", "failed"}:
            raise ValueError(f"未知回合状态: {turn_state}")
        now = _now_ms()
        plan_json = (
            json.dumps(plan_update.as_dict(), ensure_ascii=False, separators=(",", ":"))
            if plan_update is not None
            else None
        )
        with self._connect() as connection:
            session = connection.execute(
                "SELECT title, archived_at FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if session is None or session["archived_at"] is not None:
                raise KeyError(f"会话不存在或已归档: {session_id}")
            next_seq = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            title = session["title"]
            for offset, (message, visible) in enumerate(messages):
                role = message.get("role")
                if role not in {"user", "assistant", "tool"}:
                    raise ValueError(f"无法持久化未知消息角色: {role!r}")
                connection.execute(
                    """
                    INSERT INTO messages (
                        session_id, seq, submission_id, role, payload_json, created_at, visible
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        next_seq + offset,
                        submission_id,
                        role,
                        json.dumps(message, ensure_ascii=False, separators=(",", ":")),
                        now,
                        int(visible),
                    ),
                )
                if title == "新对话" and role == "user":
                    title = _title_from_message(message)
            connection.execute(
                """
                UPDATE sessions
                SET title = ?, updated_at = ?, last_turn_state = ?,
                    plan_json = COALESCE(?, plan_json)
                WHERE id = ?
                """,
                (title, now, turn_state, plan_json, session_id),
            )

    def save_compaction(self, session_id: str, number: int, summary: str) -> None:
        """Advance the active window while retaining older rows for the history UI."""

        now = _now_ms()
        with self._connect() as connection:
            active_from_seq = connection.execute(
                """
                SELECT COALESCE(MAX(seq), 1) FROM messages
                WHERE session_id = ? AND role = 'user'
                """,
                (session_id,),
            ).fetchone()[0]
            changed = connection.execute(
                """
                UPDATE sessions
                SET context_window_number = ?, context_summary = ?,
                    active_from_seq = ?, updated_at = ?
                WHERE id = ? AND archived_at IS NULL
                """,
                (number, summary, active_from_seq, now, session_id),
            ).rowcount
            if not changed:
                raise KeyError(f"会话不存在或已归档: {session_id}")

    def set_turn_state(self, session_id: str, state: str) -> None:
        self.append_messages(session_id, None, (), state)

    def set_mode(self, session_id: str, mode: ModeKind) -> None:
        """保存会话的上次选择；实际执行仍以 TurnContext 快照为准。"""

        now = _now_ms()
        with self._connect() as connection:
            changed = connection.execute(
                """
                UPDATE sessions SET mode = ?, updated_at = ?
                WHERE id = ? AND archived_at IS NULL
                """,
                (mode.value, now, session_id),
            ).rowcount
            if not changed:
                raise KeyError(f"会话不存在或已归档: {session_id}")

    def archive(self, session_id: str) -> None:
        now = _now_ms()
        with self._connect() as connection:
            changed = connection.execute(
                """
                UPDATE sessions SET archived_at = ?, updated_at = ?
                WHERE id = ? AND archived_at IS NULL
                """,
                (now, now, session_id),
            ).rowcount
            if not changed:
                raise KeyError(f"会话不存在或已归档: {session_id}")

    def memory_candidates(
        self,
        exclude_session_id: str,
        updated_before: int,
        limit: int,
    ) -> tuple[StoredSession, ...]:
        """返回尚未按当前版本提取、且已经完整结束的会话。"""

        if limit <= 0:
            raise ValueError("limit 必须大于 0")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.id FROM sessions AS s
                WHERE s.id != ?
                  AND s.last_turn_state = 'idle' AND s.updated_at <= ?
                  AND EXISTS (
                      SELECT 1 FROM messages AS msg
                      WHERE msg.session_id = s.id
                        AND msg.role = 'assistant' AND msg.visible = 1
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM memory_stage1 AS memory
                      WHERE memory.session_id = s.id
                        AND memory.source_updated_at = s.updated_at
                  )
                ORDER BY s.updated_at, s.id
                LIMIT ?
                """,
                (exclude_session_id, updated_before, limit),
            ).fetchall()
        return tuple(
            stored
            for row in rows
            if (stored := self.load(row["id"], include_archived=True)) is not None
        )

    def save_memory_extraction(
        self,
        session_id: str,
        source_updated_at: int,
        raw_memory: str,
        rollout_summary: str,
    ) -> bool:
        """仅在源会话未变化时保存，避免并发恢复会话产生过期记忆。"""

        now = _now_ms()
        with self._connect() as connection:
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO memory_stage1 (
                    session_id, source_updated_at, raw_memory,
                    rollout_summary, created_at, consolidated
                )
                SELECT id, ?, ?, ?, ?, ? FROM sessions
                WHERE id = ? AND updated_at = ?
                """,
                (
                    source_updated_at,
                    raw_memory,
                    rollout_summary,
                    now,
                    int(not raw_memory.strip()),
                    session_id,
                    source_updated_at,
                ),
            ).rowcount
        return bool(inserted)

    def memory_consolidation_needed(self) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM memory_stage1 AS memory
                WHERE memory.consolidated = 0
                LIMIT 1
                """
            ).fetchone()
        return row is not None

    def memory_outputs(self, limit: int) -> tuple[StoredMemory, ...]:
        """每个会话只选最新版本，并优先提供最近的跨会话记忆。"""

        if limit <= 0:
            raise ValueError("limit 必须大于 0")
        # ponytail: 当前按新近程度取样；有 citation 使用数据后再升级为使用频率排名。
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT memory.*, s.workspace FROM memory_stage1 AS memory
                JOIN sessions AS s ON s.id = memory.session_id
                WHERE memory.source_updated_at = (
                    SELECT MAX(latest.source_updated_at)
                    FROM memory_stage1 AS latest
                    WHERE latest.session_id = memory.session_id
                ) AND TRIM(memory.raw_memory) != ''
                ORDER BY memory.source_updated_at DESC, memory.session_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(
            StoredMemory(
                session_id=row["session_id"],
                workspace=row["workspace"],
                source_updated_at=row["source_updated_at"],
                raw_memory=row["raw_memory"],
                rollout_summary=row["rollout_summary"],
                created_at=row["created_at"],
            )
            for row in rows
        )

    def mark_memories_consolidated(self) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE memory_stage1 SET consolidated = 1")

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1, 2, 3, 4}:
                raise RuntimeError(f"不支持的会话数据库版本: {version}")
            if version == 1:
                connection.execute(
                    """
                    ALTER TABLE sessions ADD COLUMN mode TEXT NOT NULL DEFAULT 'default'
                        CHECK (mode IN ('default', 'plan'))
                    """
                )
            if version in {1, 2}:
                connection.execute("ALTER TABLE sessions ADD COLUMN plan_json TEXT")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    archived_at INTEGER,
                    model TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    context_window_number INTEGER NOT NULL DEFAULT 0,
                    context_summary TEXT,
                    active_from_seq INTEGER NOT NULL DEFAULT 1,
                    mode TEXT NOT NULL DEFAULT 'default'
                        CHECK (mode IN ('default', 'plan')),
                    plan_json TEXT,
                    last_turn_state TEXT NOT NULL DEFAULT 'idle'
                        CHECK (last_turn_state IN ('idle', 'running', 'interrupted', 'failed'))
                );
                CREATE TABLE IF NOT EXISTS messages (
                    session_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    submission_id INTEGER,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
                    payload_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    visible INTEGER NOT NULL DEFAULT 0 CHECK (visible IN (0, 1)),
                    PRIMARY KEY (session_id, seq),
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS sessions_by_recency
                    ON sessions(archived_at, updated_at DESC);
                CREATE TABLE IF NOT EXISTS memory_stage1 (
                    session_id TEXT NOT NULL,
                    source_updated_at INTEGER NOT NULL,
                    raw_memory TEXT NOT NULL,
                    rollout_summary TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    consolidated INTEGER NOT NULL DEFAULT 0 CHECK (consolidated IN (0, 1)),
                    PRIMARY KEY (session_id, source_updated_at),
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS memory_stage1_pending
                    ON memory_stage1(consolidated, created_at);
                PRAGMA user_version = 4;
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            with connection:
                yield connection
        finally:
            # sqlite3.Connection.__exit__ only commits or rolls back; Windows keeps
            # the database file locked until close() is called explicitly.
            connection.close()


def _session_from_row(
    row: sqlite3.Row, messages: tuple[StoredMessage, ...] = ()
) -> StoredSession:
    return StoredSession(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        archived_at=row["archived_at"],
        model=row["model"],
        workspace=row["workspace"],
        context_window_number=row["context_window_number"],
        context_summary=row["context_summary"],
        active_from_seq=row["active_from_seq"],
        last_turn_state=row["last_turn_state"],
        mode=ModeKind.parse(row["mode"]),
        current_plan=_plan_from_json(row["plan_json"], row["id"]),
        messages=messages,
    )


def _plan_from_json(payload_json: str | None, session_id: str) -> PlanUpdate | None:
    if payload_json is None:
        return None
    try:
        return PlanUpdate.parse(json.loads(payload_json))
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"会话 {session_id} 的当前计划已损坏") from exc


def _message_from_row(row: sqlite3.Row) -> StoredMessage:
    try:
        payload = json.loads(row["payload_json"])
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"会话 {row['session_id']} 的消息 #{row['seq']} 已损坏"
        ) from exc
    if not isinstance(payload, dict) or payload.get("role") != row["role"]:
        raise RuntimeError(f"会话 {row['session_id']} 的消息 #{row['seq']} 格式无效")
    return StoredMessage(
        seq=row["seq"],
        role=row["role"],
        payload=payload,
        created_at=row["created_at"],
        visible=bool(row["visible"]),
    )


def _title_from_message(message: dict[str, Any]) -> str:
    content = message.get("display_content")
    if (not isinstance(content, str) or not content.strip()) and isinstance(
        message.get("references"), list
    ):
        first = message["references"][0] if message["references"] else None
        if isinstance(first, dict) and isinstance(first.get("path"), str):
            return f"@{first['path']}"[:60]
    if not isinstance(content, str) or not content.strip():
        content = message.get("content")
    if not isinstance(content, str):
        return "新对话"
    return " ".join(content.split())[:60] or "新对话"


def _now_ms() -> int:
    return time.time_ns() // 1_000_000
