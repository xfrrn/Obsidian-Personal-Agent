"""持久化 Vault 文件变更，并安全撤销用户选择的路径。"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import os
import sqlite3
import time
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from uuid import uuid4


class ChangeConflictError(RuntimeError):
    """目标文件在 Agent 修改后又发生了变化。"""


class ChangeJournal:
    """同一 Vault 的写回合共享一份可恢复的前后快照。"""

    def __init__(self, workspace: Path, database: Path) -> None:
        self.workspace = workspace.resolve()
        self.database = database.resolve()
        self._turn_lock = asyncio.Lock()
        self._state_lock = RLock()
        self._active: tuple[str, int] | None = None
        self._baseline: dict[str, bytes] = {}
        self._initialize()

    async def begin_turn(self, session_id: str, submission_id: int) -> None:
        key = (session_id, submission_id)
        with self._state_lock:
            if self._active == key:
                return
        await self._turn_lock.acquire()
        try:
            baseline = self._snapshot()
            with self._state_lock:
                self._active = key
                self._baseline = baseline
        except BaseException:
            self._turn_lock.release()
            raise

    def finish_turn(self, session_id: str, submission_id: int) -> dict[str, object] | None:
        key = (session_id, submission_id)
        with self._state_lock:
            if self._active != key:
                return None
            baseline = self._baseline
        try:
            current = self._snapshot()
            changed = [path for path in baseline.keys() | current.keys() if baseline.get(path) != current.get(path)]
            if not changed:
                return None
            change_set_id = uuid4().hex
            created_at = time.time_ns() // 1_000_000
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO file_change_sets (id, session_id, submission_id, created_at) VALUES (?, ?, ?, ?)",
                    (change_set_id, session_id, submission_id, created_at),
                )
                for path in sorted(changed):
                    before = baseline.get(path)
                    after = current.get(path)
                    added, deleted, binary = _diff_stats(before, after)
                    connection.execute(
                        """
                        INSERT INTO file_changes (
                            change_set_id, path, before_content, after_content,
                            before_hash, after_hash, added_lines, deleted_lines, binary
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            change_set_id,
                            path,
                            _pack(before),
                            _pack(after),
                            _hash(before),
                            _hash(after),
                            added,
                            deleted,
                            int(binary),
                        ),
                    )
            return self.get(session_id, change_set_id, include_diff=False)
        finally:
            with self._state_lock:
                self._active = None
                self._baseline = {}
            self._turn_lock.release()

    def finish_active_session(self, session_id: str) -> None:
        with self._state_lock:
            active = self._active
        if active is not None and active[0] == session_id:
            self.finish_turn(*active)

    def list_session(self, session_id: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM file_change_sets WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [self.get(session_id, row["id"], include_diff=False) for row in rows]

    def get(
        self, session_id: str, change_set_id: str, *, include_diff: bool = True
    ) -> dict[str, object]:
        with self._connect() as connection:
            change_set = connection.execute(
                "SELECT * FROM file_change_sets WHERE id = ? AND session_id = ?",
                (change_set_id, session_id),
            ).fetchone()
            if change_set is None:
                raise KeyError("文件变更记录不存在")
            rows = connection.execute(
                "SELECT * FROM file_changes WHERE change_set_id = ? ORDER BY path",
                (change_set_id,),
            ).fetchall()

        files = []
        for row in rows:
            item: dict[str, object] = {
                "path": row["path"],
                "added": row["added_lines"],
                "deleted": row["deleted_lines"],
                "binary": bool(row["binary"]),
                "reverted": row["reverted_at"] is not None,
            }
            if include_diff:
                diff, truncated = _unified_diff(
                    row["path"], _unpack(row["before_content"]), _unpack(row["after_content"])
                )
                item.update({"diff": diff, "diff_truncated": truncated})
            files.append(item)
        return {
            "id": change_set["id"],
            "submission_id": change_set["submission_id"],
            "created_at": change_set["created_at"],
            "added": sum(int(file["added"]) for file in files),
            "deleted": sum(int(file["deleted"]) for file in files),
            "files": files,
        }

    def undo(self, session_id: str, change_set_id: str, paths: list[str]) -> dict[str, object]:
        selected = list(dict.fromkeys(paths))
        if not selected:
            raise ValueError("至少选择一个要撤销的文件")
        with self._state_lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT fc.* FROM file_changes fc
                JOIN file_change_sets cs ON cs.id = fc.change_set_id
                WHERE cs.id = ? AND cs.session_id = ?
                """,
                (change_set_id, session_id),
            ).fetchall()
            by_path = {row["path"]: row for row in rows}
            unknown = [path for path in selected if path not in by_path]
            if unknown:
                raise ValueError(f"文件不属于该变更记录: {', '.join(unknown)}")
            if any(by_path[path]["reverted_at"] is not None for path in selected):
                raise ChangeConflictError("所选文件中包含已经撤销的记录")

            current: dict[str, bytes | None] = {}
            conflicts = []
            for path in selected:
                target = self._target(path)
                value = target.read_bytes() if target.is_file() else None
                current[path] = value
                if _hash(value) != by_path[path]["after_hash"]:
                    conflicts.append(path)
            if conflicts:
                raise ChangeConflictError(
                    "这些文件在 Agent 修改后又发生了变化，未执行撤销: " + ", ".join(conflicts)
                )

            applied: list[str] = []
            try:
                for path in selected:
                    self._restore(self._target(path), _unpack(by_path[path]["before_content"]))
                    applied.append(path)
                reverted_at = time.time_ns() // 1_000_000
                connection.executemany(
                    "UPDATE file_changes SET reverted_at = ? WHERE change_set_id = ? AND path = ?",
                    ((reverted_at, change_set_id, path) for path in selected),
                )
            except BaseException:
                for path in reversed(applied):
                    self._restore(self._target(path), current[path])
                raise
        return self.get(session_id, change_set_id)

    def _snapshot(self) -> dict[str, bytes]:
        # ponytail: 个人 Vault 先用完整内存快照；实测内存成为瓶颈时再换内容寻址索引。
        snapshot: dict[str, bytes] = {}
        ignored = {".git", ".obsidian-agent-data"}
        database_files = {
            self.database,
            Path(f"{self.database}-wal"),
            Path(f"{self.database}-shm"),
        }
        for root, directories, files in os.walk(self.workspace):
            directories[:] = [name for name in directories if name not in ignored]
            root_path = Path(root)
            for name in files:
                path = root_path / name
                try:
                    resolved = path.resolve()
                    if path.is_symlink() or resolved in database_files:
                        continue
                    snapshot[resolved.relative_to(self.workspace).as_posix()] = resolved.read_bytes()
                except FileNotFoundError:
                    continue
        return snapshot

    def _target(self, relative_path: str) -> Path:
        target = (self.workspace / relative_path).resolve()
        if not target.is_relative_to(self.workspace):
            raise ValueError(f"文件路径超出 Vault: {relative_path}")
        return target

    @staticmethod
    def _restore(path: Path, content: bytes | None) -> None:
        if content is None:
            if path.exists():
                path.unlink()
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.agent-undo-{uuid4().hex}")
        try:
            temporary.write_bytes(content)
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _initialize(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS file_change_sets (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    submission_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS file_change_sets_by_session
                    ON file_change_sets(session_id, created_at);
                CREATE TABLE IF NOT EXISTS file_changes (
                    change_set_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    before_content BLOB,
                    after_content BLOB,
                    before_hash TEXT,
                    after_hash TEXT,
                    added_lines INTEGER NOT NULL,
                    deleted_lines INTEGER NOT NULL,
                    binary INTEGER NOT NULL DEFAULT 0,
                    reverted_at INTEGER,
                    PRIMARY KEY (change_set_id, path),
                    FOREIGN KEY (change_set_id) REFERENCES file_change_sets(id) ON DELETE CASCADE
                );
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=5)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            with connection:
                yield connection
        finally:
            connection.close()


def _hash(content: bytes | None) -> str | None:
    return hashlib.sha256(content).hexdigest() if content is not None else None


def _pack(content: bytes | None) -> bytes | None:
    return zlib.compress(content) if content is not None else None


def _unpack(content: bytes | None) -> bytes | None:
    return zlib.decompress(content) if content is not None else None


def _text(content: bytes | None) -> list[str] | None:
    if content is None:
        return []
    try:
        return content.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None


def _diff_stats(before: bytes | None, after: bytes | None) -> tuple[int, int, bool]:
    old_lines, new_lines = _text(before), _text(after)
    if old_lines is None or new_lines is None:
        return 0, 0, True
    added = deleted = 0
    for tag, first_start, first_end, second_start, second_end in difflib.SequenceMatcher(
        None, old_lines, new_lines, autojunk=False
    ).get_opcodes():
        if tag in {"replace", "delete"}:
            deleted += first_end - first_start
        if tag in {"replace", "insert"}:
            added += second_end - second_start
    return added, deleted, False


def _unified_diff(path: str, before: bytes | None, after: bytes | None) -> tuple[str, bool]:
    old_lines, new_lines = _text(before), _text(after)
    if old_lines is None or new_lines is None:
        return "二进制文件已修改，无法显示行级差异。", False
    diff = "\n".join(
        difflib.unified_diff(old_lines, new_lines, f"a/{path}", f"b/{path}", lineterm="")
    )
    if not diff and before != after:
        diff = "文件的换行符、末尾换行或空文件状态发生了变化。"
    limit = 500_000
    return diff[:limit], len(diff) > limit
