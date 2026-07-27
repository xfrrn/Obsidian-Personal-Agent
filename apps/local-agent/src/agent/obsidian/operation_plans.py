"""Persistent, policy-aware Vault operation plans."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
import re
import secrets
import sqlite3
from threading import Lock, RLock
from typing import Any, Mapping, Sequence
from uuid import uuid4


MAX_OPERATIONS = 10
PROTECTED_PARTS = frozenset({".obsidian", ".git", ".trash", ".obsidian-agent-data"})
ALLOWED_METADATA_TYPES = (str, int, float, bool)
DEFAULT_AUTO_ALLOW = frozenset({
    "note.create.inbox",
    "task.create.pending",
    "metadata.touch_updated_at",
})
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
TOP_LEVEL_YAML_KEY = re.compile(r"^([^\s:#][^:]*):(?:\s*(.*))?$")
DIRECTORY_SNAPSHOT = {"kind": "directory"}
DIRECTORY_HASH = "directory"


class ExecutionMode(str, Enum):
    """User-selectable write policy."""

    CONFIRM_ALL = "confirm_all"
    RISK_BASED = "risk_based"
    UNATTENDED = "unattended"


@dataclass
class ExecutionPolicy:
    """Apply one execution mode at the final write boundary."""

    mode: ExecutionMode = ExecutionMode.CONFIRM_ALL
    auto_allow: frozenset[str] = DEFAULT_AUTO_ALLOW
    max_auto_affected_files: int = 5
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def set_mode(self, mode: str | ExecutionMode) -> None:
        parsed = mode if isinstance(mode, ExecutionMode) else ExecutionMode(mode)
        with self._lock:
            self.mode = parsed

    def requires_confirmation(
        self,
        *,
        risk: str,
        capabilities: Sequence[str],
        source: str,
        affected_files: int,
    ) -> bool:
        with self._lock:
            mode = self.mode
            allowed = self.auto_allow
            max_files = self.max_auto_affected_files
        if mode is ExecutionMode.CONFIRM_ALL:
            return True
        if mode is ExecutionMode.UNATTENDED:
            return False
        if risk != "low":
            return True
        if source in {"remote", "external"} or affected_files > max_files:
            return True
        if not capabilities or not set(capabilities).issubset(allowed):
            return True
        return False


@dataclass
class PersistentOperationPlanStore:
    """Persist plans, results, snapshots and confirmation state in SQLite."""

    path: Path
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operation_plans (
                    id TEXT PRIMARY KEY,
                    plan_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    snapshot_json TEXT,
                    after_hashes_json TEXT,
                    error TEXT,
                    confirmation_hash TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    async def save(self, plan: Mapping[str, Any]) -> Mapping[str, Any]:
        stored = dict(plan)
        plan_id = str(stored.get("id") or stored.get("planId") or f"op_{uuid4().hex}")
        stored["id"] = plan_id
        stored["planId"] = plan_id
        stored.setdefault("status", "pending")
        confirmation_token = secrets.token_urlsafe(24) if stored.get("requiresConfirmation") else None
        confirmation_hash = (
            sha256(confirmation_token.encode("utf-8")).hexdigest()
            if confirmation_token
            else None
        )
        now = _now()
        with self._lock, self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO operation_plans
                    (id, plan_json, status, confirmation_hash, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (plan_id, _json(stored), stored["status"], confirmation_hash, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"operation plan already exists: {plan_id}") from exc
        if confirmation_token:
            stored["confirmationToken"] = confirmation_token
        return stored

    async def get(self, operation_plan_id: str) -> Mapping[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT plan_json, status, result_json, error FROM operation_plans WHERE id = ?",
                (operation_plan_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown operation plan: {operation_plan_id}")
            plan = json.loads(row[0])
            status = row[1]
            if status == "pending" and _expired(plan):
                status = "expired"
                connection.execute(
                    "UPDATE operation_plans SET status = ?, updated_at = ? WHERE id = ?",
                    (status, _now(), operation_plan_id),
                )
            plan["status"] = status
            if row[2]:
                plan["results"] = json.loads(row[2])
            if row[3]:
                plan["error"] = row[3]
            return plan

    async def latest_pending_for_conversation(
        self, conversation_id: str
    ) -> Mapping[str, Any] | None:
        """恢复某个会话最近的待确认计划，并签发新的单次确认令牌。"""

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT id, plan_json FROM operation_plans WHERE status = 'pending' "
                "ORDER BY updated_at DESC LIMIT 100"
            ).fetchall()
        # ponytail: plans are bounded and local; add a conversation_id column only if this scan is measured slow.
        for plan_id, payload in rows:
            plan = json.loads(payload)
            context = plan.get("context")
            if not isinstance(context, Mapping) or context.get("conversationId") != conversation_id:
                continue
            current = dict(await self.get(plan_id))
            if current.get("status") != "pending":
                continue
            if current.get("requiresConfirmation"):
                token = secrets.token_urlsafe(24)
                self.set_confirmation_token(plan_id, token)
                current["confirmationToken"] = token
            return current
        return None

    def set_confirmation_token(self, plan_id: str, token: str) -> None:
        digest = sha256(token.encode("utf-8")).hexdigest()
        self._update(plan_id, confirmation_hash=digest)

    def consume_confirmation_token(self, plan_id: str, token: str) -> bool:
        digest = sha256(token.encode("utf-8")).hexdigest()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT confirmation_hash FROM operation_plans WHERE id = ?",
                (plan_id,),
            ).fetchone()
            if row is None or not row[0] or not secrets.compare_digest(row[0], digest):
                return False
            connection.execute(
                "UPDATE operation_plans SET confirmation_hash = NULL, updated_at = ? WHERE id = ?",
                (_now(), plan_id),
            )
            return True

    def mark_running(self, plan_id: str, snapshot: Mapping[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE operation_plans
                SET status = 'running', snapshot_json = ?, error = NULL, updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (_json(snapshot), _now(), plan_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("operation plan was already claimed")

    def mark_succeeded(
        self,
        plan_id: str,
        results: Sequence[Mapping[str, Any]],
        snapshot: Mapping[str, Any],
        after_hashes: Mapping[str, str | None],
    ) -> None:
        self._update(
            plan_id,
            status="succeeded",
            result_json=_json(results),
            snapshot_json=_json(snapshot),
            after_hashes_json=_json(after_hashes),
            error=None,
        )

    def mark_failed(self, plan_id: str, status: str, error: str) -> None:
        self._update(plan_id, status=status, error=error)

    def mark_rolled_back(self, plan_id: str) -> None:
        self._update(plan_id, status="rolled_back", error=None)

    def rollback_data(self, plan_id: str) -> tuple[dict[str, Any], dict[str, str | None]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT snapshot_json, after_hashes_json FROM operation_plans WHERE id = ?",
                (plan_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown operation plan: {plan_id}")
            if not row[0] or not row[1]:
                raise ValueError("operation has no rollback snapshot")
            return json.loads(row[0]), json.loads(row[1])

    def _update(self, plan_id: str, **values: Any) -> None:
        if not values:
            return
        values["updated_at"] = _now()
        columns = ", ".join(f"{key} = ?" for key in values)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE operation_plans SET {columns} WHERE id = ?",
                (*values.values(), plan_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown operation plan: {plan_id}")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)


@dataclass
class SimpleOperationPlanner:
    """Validate operations and build an immutable, risk-scored plan."""

    root: Path
    policy: ExecutionPolicy

    async def build(
        self,
        requested_operations: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        operations = tuple(_unwrap_requested_operation(item) for item in requested_operations)
        return self.prepare(operations, context=context)

    def prepare(
        self,
        operations: Sequence[Mapping[str, Any]],
        *,
        summary: str = "准备修改知识库",
        context: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        if not operations or len(operations) > MAX_OPERATIONS:
            raise ValueError(f"operation count must be between 1 and {MAX_OPERATIONS}")
        clean_context = dict(context or {})
        allowed_paths = _string_set(clean_context.get("allowedPaths"))
        clean_operations = tuple(
            _validate_operation(self.root, operation, allowed_paths)
            for operation in operations
        )
        affected_paths = _affected_paths(clean_operations)
        capabilities = tuple(dict.fromkeys(_capability(operation) for operation in clean_operations))
        risk = _risk_of(clean_operations, clean_context)
        source = str(clean_context.get("source") or "interactive")
        requires_confirmation = self.policy.requires_confirmation(
            risk=risk,
            capabilities=capabilities,
            source=source,
            affected_files=len(affected_paths),
        )
        created_at = datetime.now(UTC)
        plan: dict[str, Any] = {
            "id": f"op_{uuid4().hex}",
            "schemaVersion": "1.0",
            "type": "operation-plan",
            "summary": summary.strip() or "准备修改知识库",
            "risk": risk,
            "requiresConfirmation": requires_confirmation,
            "capabilities": capabilities,
            "operations": clean_operations,
            "context": clean_context,
            "expectedHashes": _hash_existing(self.root, _source_paths(clean_operations)),
            "createdAt": created_at.isoformat(),
            "expiresAt": (created_at + _plan_ttl(risk)).isoformat(),
            "status": "pending",
        }
        plan["planId"] = plan["id"]
        plan["integrityHash"] = _integrity_hash(plan)
        return plan


@dataclass
class FilesystemOperationPlanExecutor:
    """Execute validated Markdown operations with conflict-safe rollback."""

    root: Path
    store: PersistentOperationPlanStore
    audit_path: Path
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    async def execute(self, plan: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        plan_id = str(plan["id"])
        with self._lock:
            _assert_executable(plan)
            operations = tuple(plan.get("operations", ()))
            paths = _affected_paths(operations)
            try:
                _assert_integrity(plan)
                _assert_expected_hashes(self.root, plan.get("expectedHashes", {}))
                snapshot = _capture(self.root, paths)
            except Exception as exc:
                self.store.mark_failed(plan_id, "invalidated", str(exc))
                self._audit("invalidated", plan, error=str(exc))
                raise
            known_hashes = _hash_state(snapshot)
            results: list[Mapping[str, Any]] = []
            self.store.mark_running(plan_id, snapshot)
            self._audit("started", plan)
            try:
                for operation in operations:
                    _assert_hash_state(
                        self.root,
                        {path: known_hashes[path] for path in _operation_paths(operation)},
                    )
                    _execute_operation(self.root, operation)
                    for path in _operation_paths(operation):
                        known_hashes[path] = _path_hash(self.root, path)
                    results.append({"status": "succeeded", "operation": operation})
                after_hashes = _capture_hashes(self.root, paths)
                self.store.mark_succeeded(plan_id, results, snapshot, after_hashes)
                self._audit("succeeded", plan, results=results, hashes=after_hashes)
                return tuple(results)
            except Exception as exc:
                message = str(exc)
                try:
                    _assert_hash_state(self.root, known_hashes)
                    _restore(self.root, snapshot)
                    self.store.mark_failed(plan_id, "rolled_back", message)
                    self._audit("rolled_back", plan, error=message)
                except Exception as rollback_exc:
                    rollback_message = f"{message}; rollback failed: {rollback_exc}"
                    self.store.mark_failed(plan_id, "rollback_failed", rollback_message)
                    self._audit("rollback_failed", plan, error=rollback_message)
                    raise RuntimeError(rollback_message) from rollback_exc
                raise

    async def rollback(self, plan: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        plan_id = str(plan["id"])
        with self._lock:
            if plan.get("status") != "succeeded":
                raise ValueError("only a succeeded operation can be rolled back")
            snapshot, after_hashes = self.store.rollback_data(plan_id)
            _assert_hash_state(self.root, after_hashes)
            _restore(self.root, snapshot)
            self.store.mark_rolled_back(plan_id)
            results = ({"status": "rolled_back", "planId": plan_id},)
            self._audit("user-rolled-back", plan, results=results)
            return results

    def _audit(
        self,
        status: str,
        plan: Mapping[str, Any],
        *,
        results: Sequence[Mapping[str, Any]] = (),
        hashes: Mapping[str, str | None] | None = None,
        error: str | None = None,
    ) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "at": _now(),
            "planId": plan.get("id"),
            "status": status,
            "risk": plan.get("risk"),
            "summary": plan.get("summary"),
            "operations": plan.get("operations"),
            "results": results,
            "hashes": hashes,
            "error": error,
        }
        with self.audit_path.open("a", encoding="utf-8", newline="\n") as file:
            file.write(_json(record) + "\n")


@dataclass
class OperationManager:
    """Single boundary for staging, authorizing, executing and rolling back plans."""

    planner: SimpleOperationPlanner
    store: PersistentOperationPlanStore
    executor: FilesystemOperationPlanExecutor
    policy: ExecutionPolicy

    async def stage(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        operations = payload.get("operations")
        if not isinstance(operations, (list, tuple)):
            raise ValueError("operations must be a list")
        context = payload.get("context") if isinstance(payload.get("context"), Mapping) else {}
        plan = self.planner.prepare(
            operations,
            summary=str(payload.get("summary") or "准备修改知识库"),
            context=context,
        )
        return dict(await self.store.save(plan))

    async def execute(
        self,
        plan_id: str,
        *,
        confirmation_token: str | None = None,
        trusted_system: bool = False,
    ) -> Mapping[str, Any]:
        plan = dict(await self.store.get(plan_id))
        if plan.get("status") != "pending":
            raise ValueError(f"operation plan is not pending: {plan.get('status')}")
        requires_confirmation = self.policy.requires_confirmation(
            risk=str(plan.get("risk")),
            capabilities=tuple(plan.get("capabilities", ())),
            source=str(dict(plan.get("context", {})).get("source") or "interactive"),
            affected_files=len(_affected_paths(tuple(plan.get("operations", ())))),
        )
        if requires_confirmation and not trusted_system:
            if not confirmation_token or not self.store.consume_confirmation_token(plan_id, confirmation_token):
                raise PermissionError("valid confirmation token is required")
        results = tuple(await self.executor.execute(plan))
        rollback_token = secrets.token_urlsafe(24)
        self.store.set_confirmation_token(plan_id, rollback_token)
        return {
            "message": f"已执行操作计划：{plan_id}",
            "operationPlanId": plan_id,
            "status": "succeeded",
            "results": results,
            "rollbackToken": rollback_token,
        }

    async def rollback(
        self,
        plan_id: str,
        *,
        confirmation_token: str | None = None,
        trusted_system: bool = False,
    ) -> Mapping[str, Any]:
        if not trusted_system and (
            not confirmation_token
            or not self.store.consume_confirmation_token(plan_id, confirmation_token)
        ):
            raise PermissionError("valid rollback confirmation token is required")
        plan = await self.store.get(plan_id)
        results = tuple(await self.executor.rollback(plan))
        return {
            "message": f"已撤销操作计划：{plan_id}",
            "operationPlanId": plan_id,
            "status": "rolled_back",
            "results": results,
        }

    def set_mode(self, mode: str) -> None:
        self.policy.set_mode(mode)


def _unwrap_requested_operation(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(value.get("type"), str):
        return value
    intent = value.get("intent")
    arguments = value.get("arguments") if isinstance(value.get("arguments"), Mapping) else {}
    if intent == "task.create":
        path = arguments.get("activeFilePath")
        title = arguments.get("taskName") or arguments.get("rawText")
        return {"type": "create-task", "path": path, "title": title}
    raise ValueError(f"write intent needs explicit operation data: {intent}")


def _validate_operation(
    root: Path,
    operation: Mapping[str, Any],
    allowed_paths: frozenset[str],
) -> Mapping[str, Any]:
    kind = operation.get("type")
    if kind == "invoke-plugin":
        raise ValueError("plugin capabilities are not supported by the P0 filesystem executor")
    if kind == "create-note":
        path = _safe_path(str(operation.get("path") or ""))
        if _file(root, path).exists():
            raise ValueError(f"note already exists: {path}")
        content = operation.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("create-note content is required")
        return {"type": kind, "path": path, "content": content}
    if kind == "create-folder":
        path = _safe_folder_path(str(operation.get("path") or ""))
        folder = _file(root, path)
        if folder.exists():
            raise ValueError(f"folder already exists: {path}")
        if folder.parent != root.resolve() and not folder.parent.is_dir():
            raise FileNotFoundError(f"parent folder does not exist: {folder.parent.relative_to(root)}")
        return {"type": kind, "path": path}
    if kind == "delete-folder":
        path = _safe_folder_path(str(operation.get("path") or ""))
        folder = _file(root, path)
        if path not in allowed_paths:
            raise PermissionError(f"operation path is outside the planning context: {path}")
        if not folder.is_dir():
            raise FileNotFoundError(path)
        if any(folder.iterdir()):
            raise ValueError(f"folder is not empty: {path}")
        return {"type": kind, "path": path}

    path = _safe_path(str(operation.get("path") or ""))
    file = _file(root, path)
    if not file.is_file():
        raise FileNotFoundError(path)
    if path not in allowed_paths:
        raise PermissionError(f"operation path is outside the planning context: {path}")

    if kind == "trash-note":
        return {"type": kind, "path": path, "trashPath": _available_trash_path(root, path)}

    if kind == "update-note":
        old_text = operation.get("oldText")
        new_text = operation.get("newText")
        if not isinstance(old_text, str) or not old_text:
            raise ValueError("update-note oldText is required")
        if not isinstance(new_text, str):
            raise ValueError("update-note newText must be a string")
        _assert_unique(file.read_text(encoding="utf-8"), old_text, path)
        return {"type": kind, "path": path, "oldText": old_text, "newText": new_text}
    if kind == "move-note":
        target = _safe_path(str(operation.get("targetPath") or ""))
        if _file(root, target).exists():
            raise ValueError(f"move target already exists: {target}")
        return {"type": kind, "path": path, "targetPath": target}
    if kind == "create-task":
        title = _single_line(operation.get("title"), "task title")
        return {"type": kind, "path": path, "title": title}
    if kind == "update-metadata":
        clean: dict[str, Any] = {"type": kind, "path": path}
        if "set" in operation:
            values = operation["set"]
            if not isinstance(values, Mapping):
                raise ValueError("metadata set must be an object")
            clean["set"] = {_metadata_key(key): _metadata_value(value) for key, value in values.items()}
        for field_name in ("remove", "addTags", "removeTags"):
            if field_name in operation:
                values = operation[field_name]
                if not isinstance(values, (list, tuple)):
                    raise ValueError(f"{field_name} must be a list")
                clean[field_name] = [
                    _metadata_key(value) if field_name == "remove" else _single_line(value, "tag")
                    for value in values
                ]
        return clean
    raise ValueError(f"unsupported operation type: {kind}")


def _risk_of(operations: Sequence[Mapping[str, Any]], context: Mapping[str, Any]) -> str:
    risk = "low"
    total_change = 0
    for operation in operations:
        kind = operation["type"]
        if kind in {"trash-note", "delete-folder"}:
            risk = "high"
        if kind in {"update-note", "move-note", "update-metadata"}:
            risk = _max_risk(risk, "medium")
        if kind == "update-note":
            total_change += len(str(operation.get("oldText", ""))) + len(str(operation.get("newText", "")))
        if kind == "create-note" and not str(operation["path"]).startswith("00-Inbox/"):
            risk = _max_risk(risk, "medium")
    if len(_affected_paths(operations)) >= 5 or total_change >= 5_000:
        risk = "high"
    if str(context.get("source") or "interactive") in {"remote", "external"}:
        risk = _max_risk(risk, "high")
    return risk


def _capability(operation: Mapping[str, Any]) -> str:
    kind = operation["type"]
    if kind == "create-note":
        return "note.create.inbox" if str(operation["path"]).startswith("00-Inbox/") else "note.create"
    if kind == "trash-note":
        return "note.trash"
    if kind == "create-folder":
        return "folder.create"
    if kind == "delete-folder":
        return "folder.delete"
    if kind == "create-task":
        return "task.create.pending"
    if kind == "move-note":
        return "note.move"
    if kind == "update-note":
        return "note.update"
    if kind == "update-metadata" and set(operation.get("set", {})) <= {"updated"} and not any(
        operation.get(key) for key in ("remove", "addTags", "removeTags")
    ):
        return "metadata.touch_updated_at"
    return "metadata.update"


def _execute_operation(root: Path, operation: Mapping[str, Any]) -> None:
    kind = operation["type"]
    path = _file(root, str(operation["path"]))
    if kind == "create-note":
        if path.exists():
            raise ValueError(f"note already exists: {operation['path']}")
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_exact(path, str(operation["content"]))
        return
    if kind == "create-folder":
        path.mkdir()
        return
    if kind == "delete-folder":
        if any(path.iterdir()):
            raise ValueError(f"folder is not empty: {operation['path']}")
        path.rmdir()
        return
    if kind == "trash-note":
        target = _file(root, str(operation["trashPath"]))
        if target.exists():
            raise ValueError(f"trash target already exists: {operation['trashPath']}")
        target.parent.mkdir(parents=True, exist_ok=True)
        path.rename(target)
        return
    if kind == "move-note":
        target = _file(root, str(operation["targetPath"]))
        if target.exists():
            raise ValueError(f"move target already exists: {operation['targetPath']}")
        target.parent.mkdir(parents=True, exist_ok=True)
        path.rename(target)
        return
    content = _read_exact(path)
    if kind == "update-note":
        old_text = str(operation["oldText"])
        _assert_unique(content, old_text, str(operation["path"]))
        content = content.replace(old_text, str(operation["newText"]), 1)
    elif kind == "create-task":
        content = f"{content.rstrip()}\n\n- [ ] {operation['title']}\n"
    elif kind == "update-metadata":
        content = _update_frontmatter(content, operation)
    else:
        raise ValueError(f"unsupported operation type: {kind}")
    _write_exact(path, content)


def _update_frontmatter(content: str, operation: Mapping[str, Any]) -> str:
    newline = "\r\n" if "\r\n" in content else "\n"
    normalized = content.replace("\r\n", "\n")
    frontmatter, body = _split_frontmatter(normalized)
    entries = _frontmatter_entries(frontmatter)
    updates = dict(operation.get("set", {}))
    remove = set(operation.get("remove", ()))
    tags = _read_tags(entries.get("tags", ()))
    for tag in operation.get("addTags", ()):
        if tag not in tags:
            tags.append(tag)
    tags = [tag for tag in tags if tag not in set(operation.get("removeTags", ()))]
    if operation.get("addTags") or operation.get("removeTags"):
        updates["tags"] = tags
    remove.update(updates)
    kept: list[str] = []
    for key, lines in entries.items():
        if key not in remove:
            kept.extend(lines)
    for key, value in updates.items():
        kept.append(f"{key}: {_yaml_value(value)}")
    # ponytail: preserve unknown YAML blocks verbatim; use a YAML library if nested field editing becomes necessary.
    rendered = "---\n" + "\n".join(kept) + "\n---\n" + body
    return rendered.replace("\n", newline)


def _split_frontmatter(content: str) -> tuple[list[str], str]:
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\n") != "---":
        return [], content
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip("\n") == "---":
            return [item.rstrip("\n") for item in lines[1:index]], "".join(lines[index + 1 :])
    return [], content


def _frontmatter_entries(lines: Sequence[str]) -> dict[str, list[str]]:
    entries: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        if match := TOP_LEVEL_YAML_KEY.match(line):
            current = match.group(1)
            entries.setdefault(current, []).append(line)
        elif current is not None:
            entries[current].append(line)
        else:
            entries.setdefault("__preamble__", []).append(line)
    return entries


def _read_tags(lines: Sequence[str]) -> list[str]:
    if not lines:
        return []
    first = lines[0].partition(":")[2].strip()
    if first.startswith("[") and first.endswith("]"):
        return [item.strip().strip("'\"") for item in first[1:-1].split(",") if item.strip()]
    if first:
        return [first.strip("'\"")]
    return [line.lstrip()[2:].strip().strip("'\"") for line in lines[1:] if line.lstrip().startswith("- ")]


def _yaml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(str(value), ensure_ascii=False)


def _source_paths(operations: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        str(operation["path"])
        for operation in operations
        if operation["type"] not in {"create-note", "create-folder"}
    ))


def _affected_paths(operations: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    paths: list[str] = []
    for operation in operations:
        paths.append(str(operation["path"]))
        if operation["type"] == "move-note":
            paths.append(str(operation["targetPath"]))
        if operation["type"] == "trash-note":
            paths.append(str(operation["trashPath"]))
    return tuple(dict.fromkeys(paths))


def _operation_paths(operation: Mapping[str, Any]) -> tuple[str, ...]:
    if operation["type"] == "move-note":
        return str(operation["path"]), str(operation["targetPath"])
    if operation["type"] == "trash-note":
        return str(operation["path"]), str(operation["trashPath"])
    return (str(operation["path"]),)


def _safe_path(value: str) -> str:
    raw = _single_line(value, "note path").replace("\\", "/")
    parts = tuple(part for part in raw.split("/") if part and part != ".")
    if (
        raw.startswith("/")
        or "://" in raw
        or re.match(r"^[A-Za-z]:", raw)
        or ".." in parts
        or any(part.casefold() in PROTECTED_PARTS for part in parts)
        or not raw.endswith(".md")
    ):
        raise ValueError(f"unsafe note path: {value}")
    return "/".join(parts)


def _safe_folder_path(value: str) -> str:
    raw = _single_line(value, "folder path").replace("\\", "/")
    parts = tuple(part for part in raw.split("/") if part and part != ".")
    if (
        not parts
        or raw.startswith("/")
        or "://" in raw
        or re.match(r"^[A-Za-z]:", raw)
        or ".." in parts
        or any(part.casefold() in PROTECTED_PARTS for part in parts)
    ):
        raise ValueError(f"unsafe folder path: {value}")
    return "/".join(parts)


def _available_trash_path(root: Path, path: str) -> str:
    original = Path(".trash") / path
    candidate = original
    index = 1
    while _file(root, candidate.as_posix()).exists():
        candidate = original.with_name(f"{original.stem}-{index}{original.suffix}")
        index += 1
    return candidate.as_posix()


def _file(root: Path, relative: str) -> Path:
    root = root.resolve()
    file = (root / relative).resolve()
    if root not in file.parents:
        raise ValueError(f"unsafe note path: {relative}")
    return file


def _single_line(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or re.search(r"[\r\n\x00-\x1f\x7f]", value):
        raise ValueError(f"{name} must be a non-empty single line")
    return value.strip()


def _metadata_key(value: Any) -> str:
    key = _single_line(value, "metadata key")
    if key == "__proto__" or ":" in key or not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise ValueError(f"unsafe metadata key: {key}")
    return key


def _metadata_value(value: Any) -> Any:
    if isinstance(value, ALLOWED_METADATA_TYPES):
        return value
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ValueError("metadata values must be scalars or string arrays")


def _assert_unique(content: str, old_text: str, path: str) -> None:
    if content.count(old_text) != 1:
        raise ValueError(f"oldText must match exactly once: {path}")


def _capture(root: Path, paths: Sequence[str]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for path in paths:
        target = _file(root, path)
        snapshot[path] = (
            _read_exact(target)
            if target.is_file()
            else DIRECTORY_SNAPSHOT
            if target.is_dir()
            else None
        )
    return snapshot


def _restore(root: Path, snapshot: Mapping[str, Any]) -> None:
    for path, content in reversed(tuple(snapshot.items())):
        file = _file(root, path)
        if content == DIRECTORY_SNAPSHOT:
            if file.exists() and not file.is_dir():
                raise ValueError(f"rollback conflict: {path}")
            file.mkdir(parents=True, exist_ok=True)
        elif content is None:
            if file.is_dir():
                file.rmdir()
            elif file.exists():
                file.unlink()
        else:
            file.parent.mkdir(parents=True, exist_ok=True)
            _write_exact(file, str(content))


def _read_exact(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as file:
        return file.read()


def _write_exact(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        file.write(content)


def _capture_hashes(root: Path, paths: Sequence[str]) -> dict[str, str | None]:
    return {path: _path_hash(root, path) for path in paths}


def _hash_existing(root: Path, paths: Sequence[str]) -> dict[str, str]:
    return {path: _required_file_hash(root, path) for path in paths}


def _path_hash(root: Path, path: str) -> str | None:
    file = _file(root, path)
    if file.is_dir():
        return DIRECTORY_HASH
    return sha256(file.read_bytes()).hexdigest() if file.is_file() else None


def _required_file_hash(root: Path, path: str) -> str:
    value = _path_hash(root, path)
    if value is None:
        raise FileNotFoundError(path)
    return value


def _hash_state(snapshot: Mapping[str, Any]) -> dict[str, str | None]:
    return {
        path: DIRECTORY_HASH
        if content == DIRECTORY_SNAPSHOT
        else sha256(str(content).encode("utf-8")).hexdigest()
        if content is not None
        else None
        for path, content in snapshot.items()
    }


def _assert_expected_hashes(root: Path, expected: Mapping[str, Any]) -> None:
    for path, digest in expected.items():
        if _required_file_hash(root, path) != digest:
            raise ValueError(f"note changed after preview: {path}")


def _assert_hash_state(root: Path, expected: Mapping[str, str | None]) -> None:
    for path, digest in expected.items():
        if _path_hash(root, path) != digest:
            raise ValueError(f"rollback conflict: {path}")


def _integrity_hash(plan: Mapping[str, Any]) -> str:
    immutable = {
        key: plan.get(key)
        for key in (
            "id",
            "schemaVersion",
            "type",
            "summary",
            "risk",
            "requiresConfirmation",
            "capabilities",
            "operations",
            "context",
            "expectedHashes",
            "createdAt",
            "expiresAt",
        )
    }
    return sha256(_json(immutable).encode("utf-8")).hexdigest()


def _assert_integrity(plan: Mapping[str, Any]) -> None:
    if not secrets.compare_digest(str(plan.get("integrityHash") or ""), _integrity_hash(plan)):
        raise ValueError("operation plan integrity check failed")


def _assert_executable(plan: Mapping[str, Any]) -> None:
    if plan.get("status") != "pending":
        raise ValueError(f"operation plan is not pending: {plan.get('status')}")
    if _expired(plan):
        raise ValueError("operation plan has expired")


def _expired(plan: Mapping[str, Any]) -> bool:
    value = plan.get("expiresAt")
    if not isinstance(value, str):
        return True
    return datetime.fromisoformat(value) <= datetime.now(UTC)


def _plan_ttl(risk: str) -> timedelta:
    return {"low": timedelta(hours=1), "medium": timedelta(minutes=15), "high": timedelta(minutes=5)}[risk]


def _max_risk(left: str, right: str) -> str:
    return left if RISK_ORDER[left] >= RISK_ORDER[right] else right


def _string_set(value: Any) -> frozenset[str]:
    if not isinstance(value, (list, tuple)):
        return frozenset()
    return frozenset(
        _safe_path(item) if item.strip().casefold().endswith(".md") else _safe_folder_path(item)
        for item in value
        if isinstance(item, str)
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
