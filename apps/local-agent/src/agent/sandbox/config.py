"""OS 沙盒后端与网络隔离配置。"""

from __future__ import annotations

from enum import Enum


class SandboxBackend(str, Enum):
    AUTO = "auto"
    NATIVE_WINDOWS = "native-windows"
    DISABLED = "disabled"

    @classmethod
    def parse(cls, value: str) -> "SandboxBackend":
        normalized = value.strip().lower()
        # 保留上一阶段公开过的配置值，但它现在映射到项目自带后端，不再寻找 Codex CLI。
        if normalized == "codex-windows":
            normalized = cls.NATIVE_WINDOWS.value
        try:
            return cls(normalized)
        except ValueError as exc:
            choices = ", ".join(backend.value for backend in cls)
            raise ValueError(f"AGENT_SANDBOX_BACKEND 必须是: {choices}") from exc


class SandboxNetwork(str, Enum):
    HOST = "host"
    BLOCKED = "blocked"

    @classmethod
    def parse(cls, value: str) -> "SandboxNetwork":
        try:
            return cls(value.strip().lower())
        except ValueError as exc:
            choices = ", ".join(policy.value for policy in cls)
            raise ValueError(f"AGENT_SANDBOX_NETWORK 必须是: {choices}") from exc
