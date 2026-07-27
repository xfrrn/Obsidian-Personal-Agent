# Personal Knowledge Agent

一个将本机 CodeX-Agent Web 控制台嵌入 Obsidian 的桌面插件。插件只负责侧栏和本机地址校验；会话、模型循环、工具、Skills、Plan Mode、权限、沙盒和持久化全部由 Python Agent 负责。

## 结构

```text
apps/
  obsidian-plugin/
    src/                    四个扁平的插件源码文件
  local-agent/src/agent/
    core/                   回合、调度和上下文压缩
    tools/                  工具、路由与执行
    permissions/ sandbox/   权限和 Windows 沙盒
    skills/                 工作区 Skills
    web/                    HTTP/SSE 服务和 React 前端
    cli/                    终端入口
tests/unit/codex_agent/     Agent 单元测试
```

Obsidian 侧栏通过 iframe 加载默认地址 `http://127.0.0.1:8000`。插件只接受 localhost、`127.0.0.1` 和 `::1`，不维护第二套 Agent 或 API 客户端。

## 运行

```powershell
python -m pip install -e apps/local-agent
$env:OPENAI_API_KEY="..."
$env:OPENAI_MODEL="model-name"
$env:AGENT_WORKSPACE="D:\Vault"
python -m agent.web.server
```

模型地址可通过 `OPENAI_BASE_URL` 配置。会话默认保存在 `~/.codex-agent/sessions.db`，Skills 从工作区的 `skills/` 加载。`apply_patch` 默认可用；Shell 工具只有在显式设置 `AGENT_ENABLE_SHELL=1` 后才注册。

## 开发

```powershell
npm run verify
npm --prefix apps/local-agent/src/agent/web/frontend run build
```

生产插件文件为 `apps/obsidian-plugin/main.js`、`manifest.json` 和 `styles.css`。
