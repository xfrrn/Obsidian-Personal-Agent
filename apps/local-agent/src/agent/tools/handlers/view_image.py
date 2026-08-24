"""把 Vault 内的图片作为多模态输入交给模型。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from agent.config.settings import Settings
from agent.permissions import ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.types import ToolExecution, ToolSpec


# ponytail: 20 MiB 足够常见 Vault 图片并限制 Base64 内存；真实大图出现时再调高。
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class ViewImageTool:
    supports_parallel_tool_calls = True
    required_access = ToolAccess.READ_ONLY
    spec = ToolSpec(
        name="view_image",
        description=(
            "读取 Agent 工作目录内的 PNG、JPEG、WebP 或 GIF 图片，并把实际图像内容交给模型分析。"
            "当用户引用图片、截图、图表或需要识别图片文字时使用；不要用 shell 读取图片二进制。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对 Agent 工作目录的图片路径。",
                },
                "detail": {
                    "type": "string",
                    "enum": ["auto", "low", "high"],
                    "description": "图片理解细节级别，默认 auto。",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    )

    def __init__(self, settings: Settings) -> None:
        self._workspace = settings.workspace.resolve()

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> ToolExecution:
        unknown = set(arguments) - {"path", "detail"}
        if unknown:
            raise ValueError(f"包含未知字段: {', '.join(sorted(unknown))}")
        target = _image_path(self._workspace, arguments.get("path"))
        detail = arguments.get("detail", "auto")
        if detail not in {"auto", "low", "high"}:
            raise ValueError("detail 必须是 auto、low 或 high")

        try:
            with target.open("rb") as image_file:
                data = image_file.read(MAX_IMAGE_BYTES + 1)
        except OSError as exc:
            raise ValueError(f"无法读取图片: {target.name}") from exc
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("图片不能超过 20 MiB")
        mime_type = _image_mime_type(data)
        if mime_type is None:
            raise ValueError("仅支持 PNG、JPEG、WebP 和 GIF 图片")

        relative_path = target.relative_to(self._workspace).as_posix()
        return ToolExecution(
            json.dumps(
                {
                    "path": relative_path,
                    "mime_type": mime_type,
                    "bytes": len(data),
                    "detail": detail,
                    "attached_to_next_model_request": True,
                },
                ensure_ascii=False,
            ),
            image_url=f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}",
            image_detail=detail,
        )


def _image_path(workspace: Path, value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("path 必须是非空字符串")
    path_text = value.strip()
    if len(path_text) > 1_000 or "\n" in path_text or "\r" in path_text:
        raise ValueError("path 不是有效路径")
    raw_path = Path(path_text)
    if raw_path.is_absolute() or raw_path.drive:
        raise ValueError("path 必须是工作目录内的相对路径")
    try:
        target = (workspace / raw_path).resolve()
    except OSError as exc:
        raise ValueError("path 不是有效路径") from exc
    if not target.is_relative_to(workspace):
        raise ValueError("path 超出工作目录")
    if not target.is_file():
        raise ValueError(f"图片不存在: {path_text}")
    return target


def _image_mime_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
