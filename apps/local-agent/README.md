# CodeX-Agent Local Service

## 启动 Web 控制台

```powershell
python -m pip install -e apps/local-agent
$env:OPENAI_API_KEY="..."
$env:OPENAI_MODEL="model-name"
npm run build:agent-ui
python -m agent.web.server --host 127.0.0.1 --port 8000
```

正常使用 Windows 一体包时不需要安装 Python 或执行上述命令：插件会启动包内 EXE。源码开发时插件回退到系统 Python；最后一条命令仅保留用于独立调试服务。

Windows Agent 由仓库根目录的 `npm run package:windows` 在隔离 venv 中构建；入口为 `scripts/windows-agent-entry.py`，系统指令和 Web 前端静态文件会一同进入包内。

终端模式使用 `python -m agent.cli.main`。模型地址、沙盒、Shell 和会话数据库分别由 `OPENAI_BASE_URL`、`AGENT_SANDBOX_*`、`AGENT_ENABLE_SHELL` 和 `AGENT_SESSION_DB` 配置。

Web 服务提供多会话、SSE 消息流、审批、权限、指标和 `/api/config` 运行时配置接口。Obsidian 插件首次连接时会把当前 Vault 设置为工作区；配置接口只接受本机请求，修改配置时不能有正在运行的回合。默认服务没有请求令牌，不要绑定到公网地址。

`obsidian_command` 始终可用且不依赖通用 Shell 开关。它通过 Obsidian 1.12.7+ 安装器自带的官方 CLI 列出或执行命令面板命令；执行动作复用现有宿主执行审批，Plan Mode 下禁止执行。

`create_frontmatter` 为 Vault 内已有的 Markdown 笔记添加固定的 `title`、`status`、`created`、`tags` frontmatter，不改正文、不重复创建，并复用现有工作区写权限和撤销记录。

`update_properties` 安全更新已有顶层 Properties；`query_tasks` 查询 Tasks 兼容任务；`mutate_task` 以路径、行号和原文复核创建或修改任务。写工具均复用现有工作区权限和撤销记录。
