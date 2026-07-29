"""从 Shell 命令中识别对 Skill 自带脚本的隐式调用。"""

from __future__ import annotations

from pathlib import Path
import re

from agent.skills.loader import Skill


_SCRIPT_PATH = re.compile(
    r'''(?ix)
    "(?P<double>[^"\r\n]+?\.(?:py|sh|js|ts|rb|pl|ps1))"
    |'(?P<single>[^'\r\n]+?\.(?:py|sh|js|ts|rb|pl|ps1))'
    |(?P<bare>[^\s;&|]+?\.(?:py|sh|js|ts|rb|pl|ps1))(?=$|[\s;&|])
    '''
)
_RUNNER = re.compile(
    r"(?i)(?<![\w.-])(?:python(?:3(?:\.\d+)*)?|py|bash|node|ruby|perl|powershell|pwsh)(?:\.exe)?(?=\s|$|[\"'])"
)
_SHELL_SEPARATOR = re.compile(r"&&|\|\||[;&|\r\n]")


def detect_implicit_skill_invocations(
    command: str,
    skills: tuple[Skill, ...],
    working_directory: Path,
) -> tuple[Skill, ...]:
    """返回命令实际启动的 Skill 脚本；结果仅用于遥测，不参与授权或执行。"""

    found: list[Skill] = []
    seen: set[str] = set()
    script_roots = tuple(
        (skill, (skill.path.parent / "scripts").resolve()) for skill in skills
    )
    for match in _SCRIPT_PATH.finditer(command):
        if not _looks_executed(command, match.start()):
            continue
        try:
            candidate = Path(next(value for value in match.groupdict().values() if value))
            if not candidate.is_absolute():
                candidate = working_directory / candidate
            candidate = candidate.resolve()
        except (OSError, ValueError):
            # 遥测解析绝不能让一条已经执行完成的工具调用反过来使回合失败。
            continue
        for skill, script_root in script_roots:
            if skill.name not in seen and candidate.is_relative_to(script_root):
                found.append(skill)
                seen.add(skill.name)
                break
    return tuple(found)


def _looks_executed(command: str, path_start: int) -> bool:
    """排除 ``cat script.py`` 一类只读引用，同时允许解释器和直接脚本调用。"""

    segment_prefix = _SHELL_SEPARATOR.split(command[:path_start])[-1]
    return bool(_RUNNER.search(segment_prefix)) or not segment_prefix.strip(" \t&(")
