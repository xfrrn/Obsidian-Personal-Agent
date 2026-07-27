"""环境变量与项目根目录 `.env` 的读取逻辑。"""

from __future__ import annotations

import codecs
import os
from pathlib import Path
import re

from agent.protocol.mode import ModeKind


_PROJECT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_INSTRUCTIONS_DIR = Path(__file__).with_name("instructions")
_PERSONALITY_NAME = re.compile(r"[a-z][a-z-]*\Z")
_PROJECT_DOC_NAMES = ("AGENTS.override.md", "AGENTS.md", "CLAUDE.md")
_PROJECT_DOC_MAX_BYTES = 32 * 1024


def load_env_file(path: Path | None = None) -> None:
    """将 ``.env`` 值补入尚未设置的进程环境变量。

    进程环境变量优先，便于 CI 或部署系统安全地注入密钥；解析器只支持安全、可预期的
    单行 ``KEY=value``，不会执行变量插值或 shell 表达式。
    """

    path = path or _PROJECT_ENV_FILE
    if not path.is_file():
        return

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()

        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or not key.replace("_", "").isalnum():
            raise ValueError(f"{path}:{line_number} 不是有效的 .env 配置")
        os.environ.setdefault(key, _parse_value(value.strip(), path, line_number))


def env_value(name: str, default: str = "") -> str:
    """集中保留环境变量读取边界，Settings 不再同时承担加载与解析职责。"""

    return os.getenv(name, default)


def env_flag(name: str, default: bool = False) -> bool:
    """只接受明确真值，避免拼写错误意外打开 shell 执行权限。"""

    return env_value(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def shell_runtime_name() -> str:
    """返回 ``create_subprocess_shell`` 在当前平台默认会使用的 shell 名称。"""

    # Windows 的 asyncio 默认读取 COMSPEC；POSIX 则由 /bin/sh 执行，不能用用户终端的 SHELL 变量猜测。
    return Path(os.environ.get("COMSPEC", "cmd.exe")).name if os.name == "nt" else "/bin/sh"


def load_project_instructions(workspace: Path) -> str:
    """读取项目根到工作目录的 AGENTS 指令，并让更深目录排在后面。

    每层只接受一个文件，避免同一目录的普通规则和临时 override 同时生效而难以判断来源。
    指令总量受限，防止仓库中的超大文档挤占模型上下文。
    """

    workspace = workspace.resolve()
    root = _find_project_root(workspace)
    remaining = _PROJECT_DOC_MAX_BYTES
    sections: list[str] = []

    for directory in _directories_from_root(root, workspace):
        if not remaining:
            break
        instruction = _read_directory_instruction(directory, root, remaining)
        if instruction is None:
            continue

        path, content, consumed = instruction
        remaining -= consumed
        sections.append(f"### {path.relative_to(root).as_posix()}\n{content}")

    if not sections:
        return ""
    return "## 工作区指令（由项目根到当前工作目录）\n\n" + "\n\n".join(sections)


def load_system_instructions(
    personality: str, additional_instructions: str = "", project_instructions: str = ""
) -> str:
    """按基础 → 人格 → 项目附加 → AGENTS → 安全的顺序组装稳定系统指令。

    人格只替换 ``base.md`` 的指定占位符，不能覆盖基础行为或安全约束。安全文件放在
    最后一段，使环境变量提供的项目提示也不会把它挤出系统指令。
    """

    if not _PERSONALITY_NAME.fullmatch(personality):
        raise ValueError("AGENT_PERSONALITY 只能包含小写字母和连字符")

    base = _read_instruction("base.md")
    personality_text = _read_instruction(f"personality/{personality}.md")
    safety = _read_instruction("safety.md")
    if "{PERSONALITY}" not in base:
        raise ValueError("base.md 必须包含 {PERSONALITY} 占位符")

    sections = [base.replace("{PERSONALITY}", personality_text)]
    if additional_instructions.strip():
        sections.append("## 项目附加指令\n" + additional_instructions.strip())
    if project_instructions.strip():
        sections.append(project_instructions.strip())
    sections.append(safety)
    return "\n\n".join(sections)


def build_mode_system_prompt(system_prompt: str, mode: ModeKind) -> str:
    """在稳定系统提示后追加当前回合模式，避免维护两套完整提示词。"""

    return f"{system_prompt}\n\n{_read_instruction(f'modes/{mode.value}.md')}"


def _read_instruction(relative_path: str) -> str:
    """只从打包的 instructions 目录读取 UTF-8 文件，缺失即失败，避免静默失去安全层。"""

    path = (_INSTRUCTIONS_DIR / relative_path).resolve()
    if not path.is_relative_to(_INSTRUCTIONS_DIR) or not path.is_file():
        raise ValueError(f"系统指令文件不存在: {relative_path}")
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"系统指令文件为空: {relative_path}")
    return content


def _find_project_root(workspace: Path) -> Path:
    """优先使用最近的 Git 根；无 Git 项目时只加载工作目录自身的规则。"""

    for directory in (workspace, *workspace.parents):
        if (directory / ".git").exists():
            return directory
    return workspace


def _directories_from_root(root: Path, workspace: Path) -> tuple[Path, ...]:
    directories = [workspace]
    while directories[-1] != root:
        directories.append(directories[-1].parent)
    return tuple(reversed(directories))


def _read_directory_instruction(
    directory: Path, root: Path, max_bytes: int
) -> tuple[Path, str, int] | None:
    """按同级优先级选择一份非空指令文件。"""

    for name in _PROJECT_DOC_NAMES:
        candidate = directory / name
        if not candidate.is_file():
            continue
        path = candidate.resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"工作区指令不能通过符号链接读取项目外文件: {candidate}")

        content, consumed = _read_limited_utf8(path, max_bytes)
        if content:
            return path, content, consumed
    return None


def _read_limited_utf8(path: Path, max_bytes: int) -> tuple[str, int]:
    """最多读取剩余预算，并在截断 UTF-8 字符时保留完整字符。"""

    with path.open("rb") as document:
        data = document.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    return decoder.decode(data, final=not truncated).strip(), len(data)


def _parse_value(value: str, path: Path, line_number: int) -> str:
    if not value:
        return ""
    if value[0] in {"'", '"'}:
        if len(value) < 2 or value[-1] != value[0]:
            raise ValueError(f"{path}:{line_number} 的引号未闭合")
        return value[1:-1]
    # 非引号值允许行尾注释，同时不影响 API Key 中不带空格的 # 字符。
    return value.split(" #", maxsplit=1)[0].rstrip()
