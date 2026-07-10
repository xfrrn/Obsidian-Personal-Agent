from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
import os
import re
import tempfile

import yaml

from oka_application.operations.errors import (
    OperationExecutorUnavailable,
    OperationPathConflict,
    OperationPostconditionFailed,
    OperationPreconditionFailed,
    OperationVersionConflict,
)
from oka_application.operations.models import (
    ExecutionContext,
    OperationError,
    OperationResult,
    OperationResultStatus,
    PreparedOperation,
    ResourceDiff,
    ResourceState,
    RollbackData,
)
from oka_application.operations.registry import OperationHandlerRegistry
from oka_domain.common import Sha256Hash, VaultPath, canonical_json, sha256_text
from oka_domain.operations import (
    ConflictPolicy,
    CreateNoteInput,
    CreateTaskInput,
    InvokePluginInput,
    KnowledgeOperation,
    MoveNoteInput,
    OperationPrecondition,
    OperationType,
    PreconditionType,
    SectionReplacement,
    TextPatch,
    UpdateMetadataInput,
    UpdateMode,
    UpdateNoteInput,
)

from .plugins import InMemoryPluginInvoker

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


class LocalFilesystemVault:
    """Safe reference Vault implementation for tests and headless low-risk execution."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, path: VaultPath) -> Path:
        candidate = (self.root / Path(*PurePosixPath(path.value).parts)).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise OperationPreconditionFailed("Vault path escapes configured root") from exc
        return candidate

    async def state(self, path: VaultPath) -> ResourceState:
        file_path = self.resolve(path)
        if not file_path.exists():
            return ResourceState(path=path, exists=False)
        text = file_path.read_text(encoding="utf-8")
        metadata, body = split_frontmatter(text)
        stat = file_path.stat()
        return ResourceState(
            path=path,
            exists=True,
            version_hash=sha256_text(text),
            content_hash=sha256_text(body),
            metadata_hash=sha256_text(canonical_json(metadata)),
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        )

    async def read(self, path: VaultPath) -> str:
        file_path = self.resolve(path)
        if not file_path.is_file():
            raise OperationPreconditionFailed(f"File does not exist: {path}")
        return file_path.read_text(encoding="utf-8")

    async def write_new(self, path: VaultPath, text: str) -> None:
        file_path = self.resolve(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with file_path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        except FileExistsError as exc:
            raise OperationPathConflict(f"Target already exists: {path}") from exc

    async def write_atomic(self, path: VaultPath, text: str) -> None:
        file_path = self.resolve(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{file_path.name}.", dir=file_path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, file_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    async def delete(self, path: VaultPath) -> None:
        file_path = self.resolve(path)
        if file_path.exists():
            file_path.unlink()

    async def move(self, source: VaultPath, target: VaultPath) -> None:
        source_file = self.resolve(source)
        target_file = self.resolve(target)
        if not source_file.is_file():
            raise OperationPreconditionFailed(f"Source does not exist: {source}")
        if target_file.exists():
            raise OperationPathConflict(f"Target already exists: {target}")
        target_file.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source_file, target_file)

    def next_available_path(self, path: VaultPath) -> VaultPath:
        source = PurePosixPath(path.value)
        for index in range(1, 10_000):
            candidate = VaultPath(str(source.with_name(f"{source.stem} ({index}){source.suffix}")))
            if not self.resolve(candidate).exists():
                return candidate
        raise OperationPathConflict(f"Unable to generate available path for {path}")

    async def link_update_plan(
        self, source: VaultPath, target: VaultPath
    ) -> dict[VaultPath, tuple[str, str]]:
        source_no_ext = str(PurePosixPath(source.value).with_suffix(""))
        target_no_ext = str(PurePosixPath(target.value).with_suffix(""))
        source_stem = PurePosixPath(source.value).stem
        target_stem = PurePosixPath(target.value).stem
        replacements = (
            (f"[[{source_no_ext}]]", f"[[{target_no_ext}]]"),
            (f"[[{source_no_ext}|", f"[[{target_no_ext}|"),
            (f"[[{source_stem}]]", f"[[{target_stem}]]"),
            (f"[[{source_stem}|", f"[[{target_stem}|"),
            (f"]({source.value})", f"]({target.value})"),
        )
        changes: dict[VaultPath, tuple[str, str]] = {}
        for file_path in self.root.rglob("*.md"):
            if not file_path.is_file():
                continue
            relative = VaultPath(file_path.relative_to(self.root).as_posix())
            if relative == source:
                continue
            before = file_path.read_text(encoding="utf-8")
            after = before
            for old, new in replacements:
                after = after.replace(old, new)
            if after != before:
                changes[relative] = (before, after)
        return changes


class LocalRuntimeInspector:
    def __init__(self, vault: LocalFilesystemVault, plugins: InMemoryPluginInvoker | None = None) -> None:
        self.vault = vault
        self.plugins = plugins or InMemoryPluginInvoker()

    async def state_for_operation(self, operation: KnowledgeOperation) -> tuple[ResourceState, ...]:
        value = operation.input
        paths = []
        for name in ("path", "source_path", "target_path", "target_path"):
            item = getattr(value, name, None)
            if isinstance(item, VaultPath) and item not in paths:
                paths.append(item)
        return tuple([await self.vault.state(path) for path in paths])

    async def check_precondition(
        self,
        precondition: OperationPrecondition,
        context: ExecutionContext,
    ) -> None:
        if precondition.type is PreconditionType.PERMISSION_GRANTED:
            if str(precondition.expected) not in context.permissions:
                raise OperationPreconditionFailed(f"Required permission missing: {precondition.expected}")
            return
        if precondition.type is PreconditionType.PLUGIN_AVAILABLE:
            plugin_id, _, capability = precondition.target.partition(":")
            from oka_domain.common import CapabilityId

            if not capability or not self.plugins.available(plugin_id, CapabilityId(capability)):
                raise OperationPreconditionFailed(f"Plugin capability unavailable: {precondition.target}")
            return
        path = VaultPath(precondition.target)
        state = await self.vault.state(path)
        if precondition.type is PreconditionType.FILE_EXISTS and not state.exists:
            raise OperationPreconditionFailed(f"File must exist: {path}")
        if precondition.type is PreconditionType.FILE_NOT_EXISTS and state.exists:
            raise OperationPreconditionFailed(f"File must not exist: {path}")
        if precondition.type is PreconditionType.FOLDER_EXISTS:
            if not self.vault.resolve(path).is_dir():
                raise OperationPreconditionFailed(f"Folder must exist: {path}")
        if precondition.type is PreconditionType.VERSION_MATCH:
            if state.version_hash is None or str(state.version_hash) != str(precondition.expected):
                raise OperationVersionConflict(f"Version mismatch: {path}")
        if precondition.type is PreconditionType.CONTENT_HASH_MATCH:
            if state.content_hash is None or str(state.content_hash) != str(precondition.expected):
                raise OperationVersionConflict(f"Content hash mismatch: {path}")
        if precondition.type is PreconditionType.METADATA_HASH_MATCH:
            if state.metadata_hash is None or str(state.metadata_hash) != str(precondition.expected):
                raise OperationVersionConflict(f"Metadata hash mismatch: {path}")


class _BaseHandler:
    def __init__(self, vault: LocalFilesystemVault) -> None:
        self.vault = vault

    async def _success(
        self,
        operation: KnowledgeOperation,
        *,
        paths: tuple[VaultPath, ...],
        before: Sha256Hash | None,
        after: Sha256Hash | None,
        metadata: Mapping[str, Any] | None = None,
    ) -> OperationResult:
        return OperationResult(
            operation_id=operation.operation_id,
            status=OperationResultStatus.COMPLETED,
            before_version_hash=before,
            after_version_hash=after,
            affected_paths=paths,
            metadata=dict(metadata or {}),
        )


class CreateNoteHandler(_BaseHandler):
    operation_type = OperationType.CREATE_NOTE

    async def prepare(self, operation: KnowledgeOperation, context: ExecutionContext) -> PreparedOperation:
        value = operation.input
        assert isinstance(value, CreateNoteInput)
        state = await self.vault.state(value.path)
        target = value.path
        if state.exists and value.conflict_policy is ConflictPolicy.FAIL:
            raise OperationPathConflict(f"Target already exists: {target}")
        if state.exists and value.conflict_policy is ConflictPolicy.RENAME:
            target = self.vault.next_available_path(target)
            state = await self.vault.state(target)
        old_text = await self.vault.read(target) if state.exists else None
        rollback = RollbackData(
            kind="restore-or-delete",
            payload={"path": str(target), "existed": state.exists, "text": old_text},
        )
        return PreparedOperation(
            operation=operation,
            before_states=(state,),
            rollback_data=rollback,
            metadata={"resolvedPath": str(target)},
        )

    async def execute(self, prepared: PreparedOperation, context: ExecutionContext) -> OperationResult:
        operation = prepared.operation
        value = operation.input
        assert isinstance(value, CreateNoteInput)
        target = VaultPath(str(prepared.metadata["resolvedPath"]))
        text = compose_frontmatter(value.frontmatter, value.content)
        before = prepared.before_states[0].version_hash
        if prepared.before_states[0].exists:
            if value.conflict_policy is ConflictPolicy.MERGE:
                previous = await self.vault.read(target)
                await self.vault.write_atomic(target, previous.rstrip() + "\n\n" + text.lstrip())
            elif value.conflict_policy is ConflictPolicy.OVERWRITE:
                await self.vault.write_atomic(target, text)
            else:
                raise OperationPathConflict(f"Unsupported existing target policy: {value.conflict_policy}")
        else:
            await self.vault.write_new(target, text)
        after = await self.vault.state(target)
        return await self._success(operation, paths=(target,), before=before, after=after.version_hash)

    async def rollback(self, operation, rollback_data, context):
        path = VaultPath(str(rollback_data.payload["path"]))
        if rollback_data.payload["existed"]:
            await self.vault.write_atomic(path, str(rollback_data.payload["text"]))
        else:
            await self.vault.delete(path)
        state = await self.vault.state(path)
        return OperationResult(
            operation_id=operation.operation_id,
            status=OperationResultStatus.ROLLED_BACK,
            affected_paths=(path,),
            after_version_hash=state.version_hash,
        )

    async def preview(self, operation, context):
        value = operation.input
        assert isinstance(value, CreateNoteInput)
        return ResourceDiff(
            path=value.path,
            change_type="create",
            after_excerpt=value.content[:500],
            metadata_changes=dict(value.frontmatter),
        )


class UpdateNoteHandler(_BaseHandler):
    operation_type = OperationType.UPDATE_NOTE

    async def prepare(self, operation, context):
        value = operation.input
        assert isinstance(value, UpdateNoteInput)
        state = await self.vault.state(value.path)
        if not state.exists:
            raise OperationPreconditionFailed(f"Note does not exist: {value.path}")
        if state.version_hash != value.expected_version_hash:
            raise OperationVersionConflict(f"Note version changed: {value.path}")
        text = await self.vault.read(value.path)
        return PreparedOperation(
            operation=operation,
            before_states=(state,),
            rollback_data=RollbackData(kind="restore-text", payload={"path": str(value.path), "text": text}),
        )

    async def execute(self, prepared, context):
        operation = prepared.operation
        value = operation.input
        assert isinstance(value, UpdateNoteInput)
        before_text = await self.vault.read(value.path)
        if value.update_mode is UpdateMode.PATCH:
            after_text = apply_patches(before_text, value.patches)
        elif value.update_mode is UpdateMode.REPLACE_SECTION:
            assert value.section is not None
            after_text = replace_markdown_section(before_text, value.section)
        elif value.update_mode is UpdateMode.REPLACE_CONTENT:
            assert value.content is not None
            metadata, _ = split_frontmatter(before_text)
            after_text = compose_frontmatter(metadata if value.preserve_frontmatter else {}, value.content)
        else:
            raise OperationPreconditionFailed(f"Unsupported update mode: {value.update_mode}")
        await self.vault.write_atomic(value.path, after_text)
        state = await self.vault.state(value.path)
        return await self._success(
            operation,
            paths=(value.path,),
            before=prepared.before_states[0].version_hash,
            after=state.version_hash,
        )

    async def rollback(self, operation, rollback_data, context):
        path = VaultPath(str(rollback_data.payload["path"]))
        await self.vault.write_atomic(path, str(rollback_data.payload["text"]))
        state = await self.vault.state(path)
        return OperationResult(
            operation_id=operation.operation_id,
            status=OperationResultStatus.ROLLED_BACK,
            affected_paths=(path,),
            after_version_hash=state.version_hash,
        )

    async def preview(self, operation, context):
        value = operation.input
        assert isinstance(value, UpdateNoteInput)
        before = await self.vault.read(value.path)
        if value.update_mode is UpdateMode.PATCH:
            after = apply_patches(before, value.patches)
        elif value.update_mode is UpdateMode.REPLACE_SECTION:
            after = replace_markdown_section(before, value.section)
        else:
            metadata, _ = split_frontmatter(before)
            after = compose_frontmatter(metadata if value.preserve_frontmatter else {}, value.content or "")
        return ResourceDiff(
            path=value.path,
            change_type="update",
            before_excerpt=before[:500],
            after_excerpt=after[:500],
        )


class MoveNoteHandler(_BaseHandler):
    operation_type = OperationType.MOVE_NOTE

    async def prepare(self, operation, context):
        value = operation.input
        assert isinstance(value, MoveNoteInput)
        source_state = await self.vault.state(value.source_path)
        if not source_state.exists:
            raise OperationPreconditionFailed(f"Source does not exist: {value.source_path}")
        if source_state.version_hash != value.expected_version_hash:
            raise OperationVersionConflict(f"Source version changed: {value.source_path}")
        target = value.target_path
        target_state = await self.vault.state(target)
        if target_state.exists:
            if value.conflict_policy is ConflictPolicy.RENAME:
                target = self.vault.next_available_path(target)
            else:
                raise OperationPathConflict(f"Target already exists: {target}")
        link_changes = await self.vault.link_update_plan(value.source_path, target) if value.update_links else {}
        rollback = RollbackData(
            kind="move-back",
            payload={
                "source": str(value.source_path),
                "target": str(target),
                "linkBackups": {str(path): before for path, (before, _) in link_changes.items()},
            },
        )
        return PreparedOperation(
            operation=operation,
            before_states=(source_state, target_state),
            rollback_data=rollback,
            metadata={"resolvedTarget": str(target), "linkChanges": link_changes},
        )

    async def execute(self, prepared, context):
        operation = prepared.operation
        value = operation.input
        assert isinstance(value, MoveNoteInput)
        target = VaultPath(str(prepared.metadata["resolvedTarget"]))
        await self.vault.move(value.source_path, target)
        affected = [value.source_path, target]
        for path, (_, after) in prepared.metadata["linkChanges"].items():
            await self.vault.write_atomic(path, after)
            affected.append(path)
        state = await self.vault.state(target)
        return await self._success(
            operation,
            paths=tuple(affected),
            before=prepared.before_states[0].version_hash,
            after=state.version_hash,
            metadata={"resolvedTarget": str(target)},
        )

    async def rollback(self, operation, rollback_data, context):
        source = VaultPath(str(rollback_data.payload["source"]))
        target = VaultPath(str(rollback_data.payload["target"]))
        if (await self.vault.state(target)).exists and not (await self.vault.state(source)).exists:
            await self.vault.move(target, source)
        affected = [source, target]
        for path_value, text in rollback_data.payload.get("linkBackups", {}).items():
            path = VaultPath(path_value)
            await self.vault.write_atomic(path, text)
            affected.append(path)
        state = await self.vault.state(source)
        return OperationResult(
            operation_id=operation.operation_id,
            status=OperationResultStatus.ROLLED_BACK,
            affected_paths=tuple(affected),
            after_version_hash=state.version_hash,
        )

    async def preview(self, operation, context):
        value = operation.input
        assert isinstance(value, MoveNoteInput)
        return ResourceDiff(
            path=value.source_path,
            change_type="move",
            before_excerpt=str(value.source_path),
            after_excerpt=str(value.target_path),
        )


class UpdateMetadataHandler(_BaseHandler):
    operation_type = OperationType.UPDATE_METADATA

    async def prepare(self, operation, context):
        value = operation.input
        assert isinstance(value, UpdateMetadataInput)
        state = await self.vault.state(value.path)
        if not state.exists:
            raise OperationPreconditionFailed(f"Note does not exist: {value.path}")
        if state.metadata_hash != value.expected_metadata_hash:
            raise OperationVersionConflict(f"Metadata changed: {value.path}")
        text = await self.vault.read(value.path)
        return PreparedOperation(
            operation=operation,
            before_states=(state,),
            rollback_data=RollbackData(kind="restore-text", payload={"path": str(value.path), "text": text}),
        )

    async def execute(self, prepared, context):
        operation = prepared.operation
        value = operation.input
        assert isinstance(value, UpdateMetadataInput)
        text = await self.vault.read(value.path)
        metadata, body = split_frontmatter(text)
        metadata.update(dict(value.set_values))
        for key in value.remove_keys:
            metadata.pop(key, None)
        tags = normalize_tags(metadata.get("tags", []))
        tags.update(str(item) for item in value.add_tags)
        tags.difference_update(str(item) for item in value.remove_tags)
        if tags:
            metadata["tags"] = sorted(tags)
        elif "tags" in metadata:
            metadata["tags"] = []
        await self.vault.write_atomic(value.path, compose_frontmatter(metadata, body))
        state = await self.vault.state(value.path)
        return await self._success(
            operation,
            paths=(value.path,),
            before=prepared.before_states[0].version_hash,
            after=state.version_hash,
        )

    async def rollback(self, operation, rollback_data, context):
        path = VaultPath(str(rollback_data.payload["path"]))
        await self.vault.write_atomic(path, str(rollback_data.payload["text"]))
        state = await self.vault.state(path)
        return OperationResult(
            operation_id=operation.operation_id,
            status=OperationResultStatus.ROLLED_BACK,
            affected_paths=(path,),
            after_version_hash=state.version_hash,
        )

    async def preview(self, operation, context):
        value = operation.input
        assert isinstance(value, UpdateMetadataInput)
        return ResourceDiff(
            path=value.path,
            change_type="metadata",
            metadata_changes={
                "set": dict(value.set_values),
                "remove": list(value.remove_keys),
                "addTags": [str(item) for item in value.add_tags],
                "removeTags": [str(item) for item in value.remove_tags],
            },
        )


class CreateTaskHandler(_BaseHandler):
    operation_type = OperationType.CREATE_TASK

    async def prepare(self, operation, context):
        value = operation.input
        assert isinstance(value, CreateTaskInput)
        state = await self.vault.state(value.target_path)
        text = await self.vault.read(value.target_path) if state.exists else None
        return PreparedOperation(
            operation=operation,
            before_states=(state,),
            rollback_data=RollbackData(
                kind="restore-or-delete",
                payload={"path": str(value.target_path), "existed": state.exists, "text": text},
            ),
        )

    async def execute(self, prepared, context):
        operation = prepared.operation
        value = operation.input
        assert isinstance(value, CreateTaskInput)
        line = render_task(value)
        state = prepared.before_states[0]
        if state.exists:
            previous = await self.vault.read(value.target_path)
            updated = previous.rstrip() + "\n" + line + "\n"
            await self.vault.write_atomic(value.target_path, updated)
        else:
            await self.vault.write_new(value.target_path, "# 任务\n\n" + line + "\n")
        after = await self.vault.state(value.target_path)
        return await self._success(
            operation,
            paths=(value.target_path,),
            before=state.version_hash,
            after=after.version_hash,
        )

    async def rollback(self, operation, rollback_data, context):
        path = VaultPath(str(rollback_data.payload["path"]))
        if rollback_data.payload["existed"]:
            await self.vault.write_atomic(path, str(rollback_data.payload["text"]))
        else:
            await self.vault.delete(path)
        state = await self.vault.state(path)
        return OperationResult(
            operation_id=operation.operation_id,
            status=OperationResultStatus.ROLLED_BACK,
            affected_paths=(path,),
            after_version_hash=state.version_hash,
        )

    async def preview(self, operation, context):
        value = operation.input
        assert isinstance(value, CreateTaskInput)
        return ResourceDiff(
            path=value.target_path,
            change_type="append-task",
            after_excerpt=render_task(value),
        )


class InvokePluginHandler:
    operation_type = OperationType.INVOKE_PLUGIN

    def __init__(self, plugins: InMemoryPluginInvoker) -> None:
        self.plugins = plugins

    async def prepare(self, operation, context):
        value = operation.input
        assert isinstance(value, InvokePluginInput)
        if not self.plugins.available(value.plugin_id, value.capability):
            raise OperationExecutorUnavailable(
                f"Plugin capability unavailable: {value.plugin_id}:{value.capability}"
            )
        rollback = None
        if self.plugins.supports_rollback(value.plugin_id, value.capability):
            rollback = RollbackData(
                kind="plugin",
                payload={
                    "pluginId": value.plugin_id,
                    "capability": str(value.capability),
                    "parameters": dict(value.parameters),
                },
            )
        return PreparedOperation(operation=operation, rollback_data=rollback)

    async def execute(self, prepared, context):
        operation = prepared.operation
        value = operation.input
        assert isinstance(value, InvokePluginInput)
        output = await self.plugins.invoke(value.plugin_id, value.capability, value.parameters)
        return OperationResult(
            operation_id=operation.operation_id,
            status=OperationResultStatus.COMPLETED,
            metadata={"output": dict(output)},
        )

    async def rollback(self, operation, rollback_data, context):
        value = operation.input
        assert isinstance(value, InvokePluginInput)
        await self.plugins.rollback(value.plugin_id, value.capability, rollback_data.payload)
        return OperationResult(
            operation_id=operation.operation_id,
            status=OperationResultStatus.ROLLED_BACK,
        )

    async def preview(self, operation, context):
        value = operation.input
        assert isinstance(value, InvokePluginInput)
        return None


def build_filesystem_handler_registry(
    vault: LocalFilesystemVault,
    plugins: InMemoryPluginInvoker | None = None,
) -> OperationHandlerRegistry:
    plugins = plugins or InMemoryPluginInvoker()
    registry = OperationHandlerRegistry()
    registry.register(OperationType.CREATE_NOTE, CreateNoteHandler(vault))
    registry.register(OperationType.UPDATE_NOTE, UpdateNoteHandler(vault))
    registry.register(OperationType.MOVE_NOTE, MoveNoteHandler(vault))
    registry.register(OperationType.UPDATE_METADATA, UpdateMetadataHandler(vault))
    registry.register(OperationType.CREATE_TASK, CreateTaskHandler(vault))
    registry.register(OperationType.INVOKE_PLUGIN, InvokePluginHandler(plugins))
    return registry


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return {}, text
    loaded = yaml.safe_load(match.group(1)) or {}
    if not isinstance(loaded, dict):
        raise OperationPreconditionFailed("YAML frontmatter must be an object")
    return dict(loaded), text[match.end() :]


def compose_frontmatter(metadata: Mapping[str, Any], body: str) -> str:
    if not metadata:
        return body
    rendered = yaml.safe_dump(
        dict(metadata),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()
    return f"---\n{rendered}\n---\n\n{body.lstrip()}"


def normalize_tags(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {item.strip().lstrip("#") for item in value.split(",") if item.strip()}
    if isinstance(value, list):
        return {str(item).strip().lstrip("#") for item in value if str(item).strip()}
    return {str(value).strip().lstrip("#")}


def apply_patches(text: str, patches: tuple[TextPatch, ...]) -> str:
    output = text
    for patch in sorted(patches, key=lambda item: item.start_offset, reverse=True):
        old = output[patch.start_offset : patch.end_offset]
        if sha256_text(old) != patch.old_text_hash:
            raise OperationVersionConflict(f"Patch source changed: {patch.patch_id}")
        output = output[: patch.start_offset] + patch.new_text + output[patch.end_offset :]
    return output


def replace_markdown_section(text: str, replacement: SectionReplacement | None) -> str:
    if replacement is None:
        raise OperationPreconditionFailed("Section replacement data is missing")
    target_path = tuple(item.strip() for item in replacement.heading_path)
    headings = []
    stack: list[str] = []
    for match in _HEADING_RE.finditer(text):
        level = len(match.group(1))
        title = match.group(2).strip()
        stack = stack[: level - 1]
        stack.append(title)
        headings.append((match, tuple(stack), level))
    selected = next((item for item in headings if item[1] == target_path), None)
    if selected is None:
        raise OperationPreconditionFailed(f"Markdown section not found: {' / '.join(target_path)}")
    match, _, level = selected
    end = len(text)
    for later, _, later_level in headings:
        if later.start() > match.start() and later_level <= level:
            end = later.start()
            break
    current = text[match.start() : end]
    if sha256_text(current) != replacement.expected_section_hash:
        raise OperationVersionConflict("Markdown section changed")
    heading_line_end = text.find("\n", match.start(), end)
    if heading_line_end == -1:
        heading_line_end = end
    if replacement.include_heading:
        new_section = replacement.new_content.rstrip() + "\n\n"
    else:
        heading = text[match.start() : heading_line_end]
        new_section = heading + "\n\n" + replacement.new_content.strip() + "\n\n"
    return text[: match.start()] + new_section + text[end:]


def render_task(value: CreateTaskInput) -> str:
    checkbox = "x" if value.status == "done" else " "
    lines = [f"- [{checkbox}] {value.title.strip()}"]
    if value.description.strip():
        lines.append(f"  - 说明：{value.description.strip()}")
    lines.append(f"  - 优先级：{value.priority}")
    lines.append(f"  - 状态：{value.status}")
    if value.project:
        lines.append(f"  - 项目：{value.project}")
    if value.due_date:
        lines.append(f"  - 截止日期：{value.due_date.date().isoformat()}")
    if value.tags:
        lines.append("  - 标签：" + " ".join(f"#{item}" for item in value.tags))
    if value.source_note_path:
        lines.append(f"  - 来源：[[{PurePosixPath(value.source_note_path.value).with_suffix('')}]]")
    return "\n".join(lines)
