"""Markdown 笔记工具共用的路径与原子写入边界。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4


def note_path(workspace: Path, value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("path 必须是非空字符串")
    path_text = value.strip()
    if len(path_text) > 1_000 or "\n" in path_text or "\r" in path_text:
        raise ValueError("path 不是有效路径")
    raw_path = Path(path_text)
    if raw_path.is_absolute() or raw_path.drive:
        raise ValueError("path 必须是 Vault 内的相对路径")
    if any(part.startswith(".") for part in raw_path.parts):
        raise ValueError("path 不能包含隐藏目录或路径跳转")
    if raw_path.suffix.lower() != ".md":
        raise ValueError("path 必须以 .md 结尾")
    try:
        target = (workspace / raw_path).resolve()
    except OSError as exc:
        raise ValueError("path 不是有效路径") from exc
    if not target.is_relative_to(workspace):
        raise ValueError("path 超出 Vault")
    return target


def read_note(target: Path) -> tuple[bytes, str]:
    if not target.is_file():
        raise ValueError(f"笔记不存在: {target.name}")
    original = target.read_bytes()
    try:
        text = original.removeprefix(b"\xef\xbb\xbf").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("笔记必须使用 UTF-8 编码") from exc
    return original, text


def write_note_if_unchanged(target: Path, original: bytes, content: bytes) -> None:
    """原子替换笔记，并拒绝覆盖读取后发生的外部修改。"""

    temporary = target.with_name(f".{target.name}.agent-{uuid4().hex}")
    try:
        temporary.write_bytes(content)
        if target.read_bytes() != original:
            raise RuntimeError("笔记在写入期间发生变化，未执行修改")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def yaml_scalar(value: str) -> str:
    ambiguous = {"null", "true", "false", "yes", "no", "on", "off", "~"}
    plain = value[0].isalpha() and all(
        character.isalnum() or character in " _-/+." for character in value
    )
    return value if plain and value.lower() not in ambiguous else json.dumps(value, ensure_ascii=False)
