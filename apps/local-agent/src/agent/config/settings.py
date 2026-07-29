"""集中读取最小 Agent 所需的环境配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent.config.loader import (
    env_flag,
    env_value,
    load_env_file,
    load_project_instructions,
    load_system_instructions,
)
from agent.permissions import ApprovalPolicy, SandboxMode
from agent.sandbox import SandboxBackend, SandboxNetwork
from agent.utils.logging import normalize_log_format, normalize_log_level


@dataclass(frozen=True, slots=True)
class Settings:
    """依赖注入到 Session 的不可变配置快照。"""

    api_key: str | None
    model: str
    base_url: str
    system_prompt: str
    workspace: Path
    shell_enabled: bool
    request_timeout_seconds: float
    sandbox_mode: SandboxMode = SandboxMode.WORKSPACE_WRITE
    approval_policy: ApprovalPolicy = ApprovalPolicy.ON_REQUEST
    sandbox_backend: SandboxBackend = SandboxBackend.AUTO
    sandbox_network: SandboxNetwork = SandboxNetwork.HOST
    sandbox_state_dir: Path = field(default_factory=lambda: _default_sandbox_state(Path.cwd()))
    context_window_tokens: int = 128_000
    reserved_output_tokens: int = 8_192
    auto_compact_token_limit: int | None = None
    log_level: str = "INFO"
    log_format: str = "text"
    temperature: float = 0.0
    session_db_path: Path = field(default_factory=lambda: _default_session_db(Path.cwd()))
    # 直接构造 Settings 的测试/嵌入方保持无外部副作用；from_env 为正常入口启用记忆。
    generate_memories: bool = False
    use_memories: bool = False
    memory_dir: Path = field(default_factory=lambda: _default_memory_dir(Path.cwd()))
    memory_idle_hours: float = 12.0
    memory_max_sessions: int = 20

    @property
    def input_token_budget(self) -> int:
        """返回本次请求可用的输入预算。"""

        return self.context_window_tokens - self.reserved_output_tokens

    @property
    def skills_dir(self) -> Path:
        """返回与会话数据库同目录、不会随插件升级覆盖的 Skill 目录。"""

        return self.session_db_path.expanduser().resolve().parent / "skills"

    @property
    def auto_compact_threshold(self) -> int:
        """返回 Codex 风格的 90% 压缩上限；配置只能让压缩更早。"""

        default_limit = min(
            self.input_token_budget,
            max(1, self.context_window_tokens * 9 // 10),
        )
        if self.auto_compact_token_limit is None:
            return default_limit
        return min(self.auto_compact_token_limit, default_limit)

    @classmethod
    def from_env(cls) -> "Settings":
        """从项目根目录的 .env 和进程环境创建配置。"""

        load_env_file()

        workspace = Path(env_value("AGENT_WORKSPACE", str(Path.cwd()))).expanduser().resolve()
        if not workspace.is_dir():
            raise ValueError(f"AGENT_WORKSPACE 不是目录: {workspace}")

        shell_enabled = env_flag("AGENT_ENABLE_SHELL")
        sandbox_mode = SandboxMode.parse(
            env_value("AGENT_SANDBOX_MODE", SandboxMode.WORKSPACE_WRITE.value)
        )
        approval_policy = ApprovalPolicy.parse(
            env_value("AGENT_APPROVAL_POLICY", ApprovalPolicy.ON_REQUEST.value)
        )
        sandbox_backend = SandboxBackend.parse(
            env_value("AGENT_SANDBOX_BACKEND", SandboxBackend.AUTO.value)
        )
        sandbox_network = SandboxNetwork.parse(
            env_value("AGENT_SANDBOX_NETWORK", SandboxNetwork.HOST.value)
        )
        sandbox_state_dir = Path(
            env_value("AGENT_SANDBOX_STATE", str(_default_sandbox_state(workspace)))
        ).expanduser().resolve()
        if (
            shell_enabled
            and sandbox_mode is not SandboxMode.DANGER_FULL_ACCESS
            and sandbox_backend is not SandboxBackend.DISABLED
            and sandbox_state_dir.is_relative_to(workspace)
        ):
            raise ValueError("AGENT_SANDBOX_STATE 必须位于模型可写工作区之外")

        timeout = float(env_value("AGENT_TIMEOUT_SECONDS", "60"))
        if timeout <= 0:
            raise ValueError("AGENT_TIMEOUT_SECONDS 必须大于 0")

        context_window_tokens = int(env_value("AGENT_CONTEXT_WINDOW_TOKENS", "128000"))
        reserved_output_tokens = int(env_value("AGENT_RESERVED_OUTPUT_TOKENS", "8192"))
        if context_window_tokens <= 0:
            raise ValueError("AGENT_CONTEXT_WINDOW_TOKENS 必须大于 0")
        if reserved_output_tokens <= 0 or reserved_output_tokens >= context_window_tokens:
            raise ValueError("AGENT_RESERVED_OUTPUT_TOKENS 必须大于 0 且小于上下文窗口")

        auto_compact_value = env_value("AGENT_AUTO_COMPACT_TOKEN_LIMIT").strip()
        auto_compact_token_limit = int(auto_compact_value) if auto_compact_value else None
        if auto_compact_token_limit is not None and auto_compact_token_limit <= 0:
            raise ValueError("AGENT_AUTO_COMPACT_TOKEN_LIMIT 必须大于 0")

        log_level = normalize_log_level(env_value("AGENT_LOG_LEVEL", "INFO"))
        log_format = normalize_log_format(env_value("AGENT_LOG_FORMAT", "text"))
        temperature = float(env_value("OPENAI_TEMPERATURE", "0"))
        if not 0 <= temperature <= 2:
            raise ValueError("OPENAI_TEMPERATURE 必须在 0 到 2 之间")

        memory_idle_hours = float(env_value("AGENT_MEMORY_IDLE_HOURS", "12"))
        memory_max_sessions = int(env_value("AGENT_MEMORY_MAX_SESSIONS", "20"))
        if memory_idle_hours < 0:
            raise ValueError("AGENT_MEMORY_IDLE_HOURS 不能小于 0")
        if memory_max_sessions <= 0:
            raise ValueError("AGENT_MEMORY_MAX_SESSIONS 必须大于 0")

        return cls(
            api_key=env_value("OPENAI_API_KEY") or None,
            # AGENT_MODEL 保持旧配置兼容；OPENAI_MODEL 让密钥、地址、模型使用同一命名约定。
            model=env_value("AGENT_MODEL") or env_value("OPENAI_MODEL") or "gpt-4.1-mini",
            base_url=env_value("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            system_prompt=load_system_instructions(
                env_value("AGENT_PERSONALITY", "default"),
                env_value("AGENT_SYSTEM_PROMPT"),
                load_project_instructions(workspace),
            ),
            workspace=workspace,
            # Shell 能执行任意命令，默认关闭。只有本地使用者显式同意才会注册该工具。
            shell_enabled=shell_enabled,
            request_timeout_seconds=timeout,
            sandbox_mode=sandbox_mode,
            approval_policy=approval_policy,
            sandbox_backend=sandbox_backend,
            sandbox_network=sandbox_network,
            sandbox_state_dir=sandbox_state_dir,
            context_window_tokens=context_window_tokens,
            reserved_output_tokens=reserved_output_tokens,
            auto_compact_token_limit=auto_compact_token_limit,
            log_level=log_level,
            log_format=log_format,
            temperature=temperature,
            session_db_path=Path(
                env_value(
                    "AGENT_SESSION_DB",
                    str(_default_session_db(workspace)),
                )
            ).expanduser().resolve(),
            generate_memories=env_flag("AGENT_GENERATE_MEMORIES", True),
            use_memories=env_flag("AGENT_USE_MEMORIES", True),
            memory_dir=Path(
                env_value("AGENT_MEMORY_DIR", str(_default_memory_dir(workspace)))
            ).expanduser().resolve(),
            memory_idle_hours=memory_idle_hours,
            memory_max_sessions=memory_max_sessions,
        )


def _default_session_db(workspace: Path) -> Path:
    """Prefer per-user state, but keep embedded environments without a home usable."""

    try:
        return Path.home() / ".codex-agent" / "sessions.db"
    except RuntimeError:
        return workspace / ".agent" / "sessions.db"


def _default_memory_dir(workspace: Path) -> Path:
    """与会话数据库共用用户状态根，避免把个人记忆写进项目仓库。"""

    try:
        return Path.home() / ".codex-agent" / "memories"
    except RuntimeError:
        return workspace / ".agent" / "memories"


def _default_sandbox_state(workspace: Path) -> Path:
    """Keep sandbox capability state outside the model-writable workspace when possible."""

    try:
        return Path.home() / ".codex-agent" / "sandbox"
    except RuntimeError:
        return workspace.parent / ".codex-agent-sandbox"
