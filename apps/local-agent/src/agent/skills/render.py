"""将 Skill 元数据渲染为模型可见的精简目录。"""

from __future__ import annotations

import logging

from agent.skills.loader import Skill


_LOGGER = logging.getLogger(__name__)
_MAX_AVAILABLE_SKILLS_BYTES = 8_000
_HEADER = """<skills_instructions>
## Skills

Skill 是存放在 SKILL.md 中的工作流说明，不是 function-call 工具。

### 使用规则
- 触发规则：用户使用 `$skill-name`、直接提到 Skill 名，或当前任务明显匹配下方描述时，本回合必须使用该 Skill。
- 决定使用后，如果本轮没有注入对应的 `<skill>` 正文，先通过可用的只读文件能力完整读取目录中给出的 SKILL.md，再执行任务。
- SKILL.md 中的相对路径以该文件所在目录为基准；按需读取 references/、assets/ 等资源。
- 如果存在 scripts/，优先运行或修改已有脚本，不要重新抄写其中的大段代码。
- 如果没有文件读取能力，请让用户用 `$skill-name` 显式选择，使系统在下一轮注入正文。

### 可用 Skills
"""
_FOOTER = "</skills_instructions>"
_SHORTENED_NOTICE = "- 注意：Skill 描述已缩短，以适应目录上下文预算。\n"


def render_available_skills(skills: tuple[Skill, ...]) -> str | None:
    """按输入优先级渲染有界目录；显式 mention 始终使用完整 Skill 快照。"""

    if not skills:
        return None

    reserved = len((_HEADER + _FOOTER).encode("utf-8"))
    body_budget = _MAX_AVAILABLE_SKILLS_BYTES - reserved
    full_lines = tuple(_render_line(skill, skill.description) for skill in skills)
    if _byte_size(full_lines) <= body_budget:
        return _HEADER + "".join(full_lines) + _FOOTER

    minimum_lines = tuple(_render_line(skill, "") for skill in skills)
    shortened_budget = body_budget - len(_SHORTENED_NOTICE.encode("utf-8"))
    if _byte_size(minimum_lines) <= shortened_budget:
        lines = _shorten_descriptions_fairly(skills, shortened_budget)
        _LOGGER.warning("skills.catalog_truncated")
        return _HEADER + "".join(lines) + _SHORTENED_NOTICE + _FOOTER

    # 输入顺序代表来源优先级。当前 loader 只有一个根，因此稳定路径顺序
    # 同时就是超限时的保留顺序；未来增加多根目录时应在进入 renderer 前排序。
    prefix_sizes = [0]
    for line in minimum_lines:
        prefix_sizes.append(prefix_sizes[-1] + len(line.encode("utf-8")))
    for included in range(len(skills) - 1, -1, -1):
        omitted = len(skills) - included
        notice = _omitted_notice(omitted)
        if prefix_sizes[included] + len(notice.encode("utf-8")) <= body_budget:
            _LOGGER.warning("skills.catalog_truncated")
            return _HEADER + "".join(minimum_lines[:included]) + notice + _FOOTER

    raise AssertionError("Skill 目录固定说明超过预算")


def _shorten_descriptions_fairly(
    skills: tuple[Skill, ...], body_budget: int
) -> tuple[str, ...]:
    """用共同字节上限分配描述预算，短描述省下的空间会自动让给长描述。"""

    low = 0
    high = max(len(skill.description.encode("utf-8")) for skill in skills)
    best = tuple(_render_line(skill, "") for skill in skills)
    while low <= high:
        limit = (low + high) // 2
        lines = tuple(
            _render_line(skill, _fit_utf8(skill.description, limit)) for skill in skills
        )
        if _byte_size(lines) <= body_budget:
            best = lines
            low = limit + 1
        else:
            high = limit - 1
    return best


def _render_line(skill: Skill, description: str) -> str:
    detail = f"{description} " if description else ""
    return f"- ${skill.name}: {detail}(file: {skill.path})\n"


def _omitted_notice(count: int) -> str:
    return (
        "- 注意：目录超过上下文预算，所有描述已移除，另有 "
        f"{count} 个 Skill 未显示；用户仍可用 `$name` 显式选择未显示项。\n"
    )


def _byte_size(lines: tuple[str, ...]) -> int:
    return sum(len(line.encode("utf-8")) for line in lines)


def _fit_utf8(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    ellipsis = "…"
    ellipsis_bytes = len(ellipsis.encode("utf-8"))
    if limit < ellipsis_bytes:
        return ""
    return encoded[: limit - ellipsis_bytes].decode("utf-8", "ignore").rstrip() + ellipsis
