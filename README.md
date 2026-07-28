# Personal Knowledge Agent

一个把 CodeX-Agent React 界面直接挂载到 Obsidian 侧栏的桌面插件。插件负责界面、本机地址校验、配置同步和 Agent 进程托管；会话、模型循环、工具、Skills、Plan Mode、权限、沙盒和持久化由 Python Agent 负责。

## 结构

```text
apps/
  obsidian-plugin/
    src/                    插件宿主、侧栏、设置与进程托管
    ui/                     直接挂载到 ItemView 的 React 界面
  local-agent/src/agent/
    core/                   回合、调度和上下文压缩
    tools/                  工具、路由与执行
    permissions/ sandbox/   权限和 Windows 沙盒
    skills/                 工作区 Skills
    web/                    JSON API 和 SSE 服务
    cli/                    终端入口
tests/unit/codex_agent/     Agent 单元测试
```

React 界面编译进插件 `main.js`，通过 Shadow DOM 直接挂载到 Obsidian `ItemView`，保留原前端样式且不污染 Obsidian；Python 服务只提供 JSON API 和 SSE，不再提供网页。默认 Agent 地址为 `http://127.0.0.1:8000`，插件只接受 localhost、`127.0.0.1` 和 `::1`。

模型地址、模型、工作区、权限、Shell 和会话数据库路径在插件设置面板持久化；API Key 单独保存在 Obsidian SecretStorage。侧栏打开或重新加载时，插件通过本机 `/api/config` 把配置同步给空闲的 Agent 运行时。

`设置 → Personal Knowledge Agent → Agent 启动地址` 可修改本机监听地址和端口，例如 `http://127.0.0.1:8765`。保存后，插件会按新地址重启自己创建的 Agent；手动运行的外部 Agent 不会被终止。

## Windows 一体包

在 64 位 Windows 开发机运行：

```powershell
npm run package:windows
```

构建机需要 Node.js 20+、64 位 Python、`venv` 和 `pip`；脚本会在 `.package-build` 创建隔离环境、执行完整测试并打包，不会向系统 Python 安装运行依赖。

产物位于 `dist/personal-knowledge-agent-windows-x64-<version>.zip`。将 ZIP 解压到 Vault 的 `.obsidian/plugins`，确认形成 `.obsidian/plugins/personal-knowledge-agent/manifest.json`，再在 Obsidian 中启用插件。目标电脑不需要安装 Python、Node.js 或 Agent 依赖。

一体包内的 `agent/codex-agent.exe` 尚未进行代码签名，仅适合个人侧载；公开分发前应增加 Windows 代码签名。

## 源码开发

```powershell
python -m pip install -e apps/local-agent
$env:OPENAI_API_KEY="..."
$env:OPENAI_MODEL="model-name"
npm run build
```

以上安装与构建只需在源码开发环境准备。`npm run build` 会先构建 React 界面，再将界面与样式编译进插件。插件加载时会探测配置的本机地址；服务未运行时，一体包优先启动内置 EXE，源码环境回退到 `python -m agent.web.server`。插件卸载时关闭自己创建的进程，手动启动的已有服务会直接复用且不会被关闭。

插件首次加载时自动把当前 Vault 根目录设置为 Agent 工作区，不需要配置 `AGENT_WORKSPACE`。其他环境变量仍是服务首次启动时的后备配置；插件面板保存过配置后，以插件配置为准。会话默认保存在 `~/.codex-agent/sessions.db`，Skills 从工作区的 `skills/` 加载。`apply_patch` 默认可用，Shell 工具由插件设置开关控制。

### 调用 Obsidian 命令

内置 `obsidian_command` 工具可以列出并执行 Obsidian 命令面板中的命令，包括第三方插件注册的命令。它不依赖 Agent 的 Shell 开关；执行命令时仍遵循当前宿主执行权限和单次审批策略。

该工具使用 Obsidian 官方 CLI。无需安装额外 CLI 包，但 Windows 必须使用 Obsidian 1.12.7+ 安装器，并在 `设置 → 常规 → 命令行界面` 中启用 CLI。重启 PowerShell 后验证：

```powershell
obsidian version
obsidian commands filter=linter
```

工具每次调用都会重新读取 Windows 用户和系统 PATH，因此启用 CLI 后不必重启 Agent。如果命令仍不可用，工具会返回上述安装与启用提示。当前版本只支持列出命令和按命令 ID 执行，不包含 MCP、插件私有 API 或专用插件适配器。

### 创建笔记 Frontmatter

内置 `create_frontmatter` 工具为当前 Vault 中已有的 Markdown 笔记添加 YAML Properties，参数为 `path`、`title`、`status` 和可选的 `tags`。`status` 支持 `todo`、`doing`、`done`；`created` 使用本机当天日期自动生成，字段固定按 `title`、`status`、`created`、`tags` 排列。

工具只接受 Vault 内已有的 `.md` 相对路径，不允许写入隐藏目录；笔记已有 frontmatter 时拒绝重复创建，正文保持不变。写入遵循现有工作区权限，Plan Mode 和只读模式下禁止执行，变更可通过现有文件记录撤销。它负责字段语义和初始结构；Linter 可以在之后继续统一空格、换行等格式，两者不冲突。

### Properties 与 Tasks

`update_properties` 更新已有 frontmatter 的顶层字段，支持字符串、数字、布尔值和字符串数组；正文及未指定字段保持不变。复杂嵌套 YAML 不在首版写入范围内。

`query_tasks` 直接查询 Vault 中的 Markdown 任务，可按完成状态、路径前缀、截止日期和标签过滤。`mutate_task` 支持创建、完成、重新打开、改期和设置优先级；修改既有任务必须提交 `query_tasks` 返回的行号和原文，文件变化后会拒绝误写。循环任务的完成仍交给 Tasks 插件，以保留其生成下一次任务的行为。

三个工具都直接维护 Markdown，不依赖 Bases 或 Tasks 私有 API。Properties 修改会自动反映到 Bases；需要统一格式时，在一次 Agent 操作结束后再通过 `obsidian_command` 运行 Linter。

## 开发

```powershell
npm run verify
npm run build:agent-ui
npm run package:windows
```

源码插件文件为 `apps/obsidian-plugin/main.js`、`manifest.json` 和 `styles.css`；Windows 一体包还包含 `agent/codex-agent.exe` 及其 `_internal` 运行目录。
