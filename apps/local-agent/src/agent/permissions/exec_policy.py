"""跨 shell 的高危命令拒绝策略。"""

from __future__ import annotations

import re


# 这里只拦截跨 shell 都明确危险的构造；普通命令的文件和网络边界仍由 OS 沙盒负责。
# 尝试在字符串层完整解析 PowerShell/cmd/POSIX shell 会制造更容易绕过的伪安全感。
_FORBIDDEN_COMMAND_PATTERNS = (
    (
        re.compile(
            r"\b(?:curl|wget|invoke-webrequest|iwr)\b[^|]*\|\s*"
            r"(?:sh|bash|zsh|cmd|powershell|pwsh|python3?|node|ruby|perl|"
            r"iex|invoke-expression)(?:\.exe)?(?:\s|$)",
            re.IGNORECASE | re.DOTALL,
        ),
        "不允许把远程响应直接管道到命令解释器",
    ),
    (
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
        "不允许执行 fork bomb",
    ),
    (
        re.compile(
            r"(?:^|[;&|()\r\n]\s*)(?:sudo|doas|runas)(?:\.exe)?(?:\s|$)",
            re.IGNORECASE,
        ),
        "不允许通过 shell 提升系统权限",
    ),
    (
        re.compile(
            r"(?:^|[;&|()\r\n]\s*)start-process\b[^;&|\r\n]*"
            r"-verb\s+runas(?:\s|$)",
            re.IGNORECASE,
        ),
        "不允许通过 shell 提升系统权限",
    ),
    (
        re.compile(
            r"(?:^|[;&|()\r\n]\s*)chmod(?:\.exe)?(?:\s+-\S+)*\s+0?777(?:\s|$)",
            re.IGNORECASE,
        ),
        "不允许把文件权限放宽为 777",
    ),
    (
        re.compile(
            r"(?:^|[;&|()\r\n]\s*)rm(?:\.exe)?\s+"
            r"(?=[^;&|\r\n]*(?:--recursive|-[a-z]*r[a-z]*))"
            r"(?=[^;&|\r\n]*(?:--force|-[a-z]*f[a-z]*))",
            re.IGNORECASE,
        ),
        "不允许递归强制删除",
    ),
    (
        re.compile(
            r"(?:^|[;&|()\r\n]\s*)remove-item(?:\s|$)"
            r"(?=[^;&|\r\n]*-(?:recurse|r)\b)"
            r"(?=[^;&|\r\n]*-(?:force|fo)\b)",
            re.IGNORECASE,
        ),
        "不允许递归强制删除",
    ),
    (
        re.compile(
            r"(?:^|[;&|()\r\n]\s*)(?:rmdir|rd|del|erase)(?:\.exe)?\s+"
            r"(?=[^;&|\r\n]*/s\b)(?=[^;&|\r\n]*/q\b)",
            re.IGNORECASE,
        ),
        "不允许静默递归删除目录",
    ),
    (
        re.compile(
            r"(?:^|[;&|()\r\n]\s*)"
            r"(?:mkfs(?:\.[\w-]+)?|diskpart|clear-disk|initialize-disk)(?:\s|$)",
            re.IGNORECASE,
        ),
        "不允许格式化或重分区磁盘",
    ),
    (
        re.compile(
            r"(?:^|[;&|()\r\n]\s*)format(?:\.com)?\s+[a-z]:(?:\s|$)",
            re.IGNORECASE,
        ),
        "不允许格式化或重分区磁盘",
    ),
    (
        re.compile(
            r"(?:^|[;&|()\r\n]\s*)dd\b[^;&|\r\n]*\bof=/dev/",
            re.IGNORECASE,
        ),
        "不允许直接覆写块设备",
    ),
)


def command_denial_reason(command: str) -> str | None:
    """返回首个命中的拒绝原因；普通命令交由既有沙盒和能力策略判断。"""

    return next(
        (reason for pattern, reason in _FORBIDDEN_COMMAND_PATTERNS if pattern.search(command)),
        None,
    )
