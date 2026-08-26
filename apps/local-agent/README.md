# CodeX-Agent Local Service

## 启动 API 后端

```powershell
python -m pip install -e apps/local-agent
$env:OPENAI_API_KEY="..."
$env:OPENAI_MODEL="model-name"
python -m agent.web.server --host 127.0.0.1 --port 8000
```

正常使用 Windows 一体包时不需要安装 Python 或执行上述命令：插件会启动包内 EXE。源码开发时插件回退到系统 Python；最后一条命令仅用于独立调试 API。

Windows Agent 由仓库根目录的 `npm run package:windows` 在隔离 venv 中构建；入口为 `scripts/windows-agent-entry.py`。Python 包只包含后端和系统指令，React 界面与样式编译在 Obsidian 插件的 `main.js` 中。

终端模式使用 `python -m agent.cli.main`。模型地址、沙盒、Shell 和会话数据库分别由 `OPENAI_BASE_URL`、`AGENT_SANDBOX_*`、`AGENT_ENABLE_SHELL` 和 `AGENT_SESSION_DB` 配置；`AGENT_DISABLED_SKILLS` 接受逗号分隔的 Skill 名称；跨会话记忆默认启用，可通过 `AGENT_GENERATE_MEMORIES`、`AGENT_USE_MEMORIES` 和 `AGENT_MEMORY_*` 调整。

MCP Host 默认读取 `~/.codex-agent/config.toml`，可用 `AGENT_MCP_CONFIG` 指向其他文件。配置采用 Codex 的 `[mcp_servers.<name>]` 结构，支持 stdio、Streamable HTTP、Bearer/OAuth、Tools、Resources、Server Instructions、工具过滤、超时和 `auto`、`prompt`、`writes`、`approve` 审批模式。普通用户可在 Obsidian 设置页的“MCP 服务”直接添加、编辑、启停、删除和登录；以下 TOML 仅用于高级配置或手动部署：

```toml
[mcp_servers.example]
command = "python"
args = ["path/to/server.py"]
env_vars = ["EXAMPLE_TOKEN"]
enabled_tools = ["read", "write"]
default_tools_approval_mode = "writes"
startup_timeout_sec = 10
tool_timeout_sec = 60

[mcp_servers.example.tools.write]
approval_mode = "prompt"
```

远程 Server 改用 `url`。Bearer Token 只配置环境变量名，并优先于 OAuth：

```toml
[mcp_servers.remote]
url = "https://example.com/mcp"
bearer_token_env_var = "EXAMPLE_TOKEN"
http_headers = { X-Client = "obsidian" }
env_http_headers = { X-Workspace-Token = "WORKSPACE_TOKEN" }
```

需要 OAuth 时不要设置 Bearer；动态客户端注册只需配置 URL，然后在 Obsidian 设置页点击“授权”。服务要求固定客户端 ID 或 scopes 时：

```toml
mcp_oauth_callback_port = 8000

[mcp_servers.remote]
url = "https://example.com/mcp"
scopes = ["read", "write"]
oauth_resource = "https://example.com/"

[mcp_servers.remote.oauth]
client_id = "registered-client-id"
callback_port = 8000
```

`callback_port` 必须与 Agent 当前监听端口一致；使用反向代理时可改设完整的 `mcp_oauth_callback_url`。OAuth 凭据在 Windows 通过当前用户 DPAPI 加密，其他系统写入仅当前用户可读的文件。Agent 会向模型注册 `list_mcp_resources`、`list_mcp_resource_templates` 和 `read_mcp_resource`；MCP Prompts 不直接变成模型工具，未声明的 Sampling、Elicitation 和 Roots 客户端能力会按协议由 Server 跳过。

内置 `$skill-installer` 通过已有 Shell 工具列出或安装 GitHub Skills，目标固定为会话数据库同目录的 `skills/`。安装经过现有宿主执行审批，同名目录不会覆盖；文件写入后由每回合的 Skill 快照自动发现。Windows 包内脚本仅依赖 PowerShell。

HTTP 服务提供多会话、SSE 消息流、审批、权限、指标和 `/api/config` 运行时配置接口，根路径返回 404。Obsidian 插件首次连接时会把当前 Vault 设置为工作区；服务只为 `app://obsidian.md` 返回跨源许可，其他网页来源仍被浏览器拦截。配置接口只接受本机请求，修改配置时不能有正在运行的回合；默认服务没有请求令牌，不要绑定到公网地址。

`obsidian_command` 始终可用且不依赖通用 Shell 开关。它通过 Obsidian 1.12.7+ 安装器自带的官方 CLI 列出或执行命令面板命令；执行动作复用现有宿主执行审批，Plan Mode 下禁止执行。

`create_frontmatter` 为 Vault 内已有的 Markdown 笔记添加固定的 `title`、`status`、`created`、`tags` frontmatter，不改正文、不重复创建，并复用现有工作区写权限和撤销记录。

`update_properties` 安全更新已有顶层 Properties；`query_tasks` 查询 Tasks 兼容任务；`mutate_task` 以路径、行号和原文复核创建或修改任务。写工具均复用现有工作区权限和撤销记录。
