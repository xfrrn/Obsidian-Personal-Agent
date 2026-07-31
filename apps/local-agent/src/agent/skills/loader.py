"""项目内 ``skills/*/SKILL.md`` 的发现与最小 front matter 解析。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
import re

import yaml


_LOGGER = logging.getLogger(__name__)
_NAME = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
_MAX_SKILL_BYTES = 64 * 1024


def is_valid_skill_name(name: str) -> bool:
    return bool(_NAME.fullmatch(name))


@dataclass(frozen=True, slots=True)
class Skill:
    """目录页需要的轻量元数据；正文只在用户显式选择后才读取。"""

    name: str
    description: str
    path: Path


def skill_fingerprint(root: Path) -> tuple[tuple[str, int, int], ...]:
    """返回文件版本指纹，供 SkillsService 判断是否需要重新解析目录。"""

    if not root.exists():
        return ()
    if not root.is_dir():
        raise ValueError(f"Skill 根路径不是目录: {root}")
    return tuple(
        (str(path), path.stat().st_mtime_ns, path.stat().st_size)
        for path in sorted(root.glob("*/SKILL.md"))
    )


def discover_skills(root: Path) -> tuple[Skill, ...]:
    """扫描一层 Skill 目录，跳过重复名称或格式错误的 SKILL.md。"""

    root = root.resolve()
    skills: list[Skill] = []
    names: set[str] = set()
    for path_text, _, _ in skill_fingerprint(root):
        try:
            path = Path(path_text).resolve()
            if not path.is_relative_to(root):
                raise ValueError(f"Skill 文件超出根目录: {path}")
            metadata, _ = _read_document(path)
            name = metadata.get("name", "")
            description = metadata.get("description", "")
            if not isinstance(name, str) or not is_valid_skill_name(name):
                raise ValueError(f"{path} 的 name 必须是小写字母、数字或连字符")
            if not isinstance(description, str) or not description.strip():
                raise ValueError(f"{path} 缺少 description")
            if name in names:
                raise ValueError(f"重复的 Skill 名称: {name}")
        except (OSError, UnicodeError, ValueError) as exc:
            _LOGGER.warning(
                "skills.invalid_skipped",
                extra={"path": path_text, "error": str(exc)},
            )
            continue
        names.add(name)
        skills.append(Skill(name=name, description=description.strip(), path=path))
    return tuple(skills)


def load_instructions(skill: Skill) -> str:
    """仅在显式提及时读取正文，避免把所有 Skill 全文送入模型上下文。"""

    _, instructions = _read_document(skill.path)
    return instructions


def _read_document(path: Path) -> tuple[dict[object, object], str]:
    if path.stat().st_size > _MAX_SKILL_BYTES:
        raise ValueError(f"{path} 超过 {_MAX_SKILL_BYTES // 1024} KiB 上限")
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path} 必须以 YAML 风格 front matter 开始")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise ValueError(f"{path} 的 front matter 未闭合") from exc

    try:
        metadata = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path} 的 front matter 格式无效") from exc
    if not isinstance(metadata, dict):
        raise ValueError(f"{path} 的 front matter 必须是 YAML 映射")
    instructions = "\n".join(lines[end + 1 :]).strip()
    if not instructions:
        raise ValueError(f"{path} 缺少 Skill 正文")
    return metadata, instructions
