# Personal Knowledge Agent

一个以 Markdown 为事实源、由本机 Python Agent 驱动的 Obsidian 知识助手。插件负责原生交互，Agent 负责会话、模型循环、工具、Skills、Plan Mode、压缩和持久化；Vault 写入始终经过 OperationPlan 预览、确认、审计与回滚。

## 结构

```text
apps/
  obsidian-plugin/              Obsidian 原生侧栏、设置和 SSE 客户端
  local-agent/src/agent/        从 CodeX-Agent bdbd148 迁入的唯一 Agent 内核
  local-agent/src/main.py       本机鉴权 HTTP/SSE 服务
apps/local-agent/src/agent/obsidian/
                                Vault 工具、OperationPlan 与运行时装配
```

详细边界见 [Agent 设计](docs/agent-design.md)。

## 已实现

- 多会话创建、切换、归档、持久化恢复和同会话 steering。
- 流式回答、工具轨迹、Agent 工作计划、Default/Plan 模式。
- 当前笔记、选中文本、显式 `@` 引用和全库范围上下文。
- `search_notes`、`read_note`、任务、项目、规则与知识分析工具。
- Vault `skills/*/SKILL.md` 动态发现。
- 独立 OperationPlan 预览、确认、执行、冲突拒绝、回滚和审计。
- API Key 只从 Obsidian SecretStorage 读取，并在握手时传入 Agent 内存。

模型不能使用 `apply_patch`、`exec_command`、`write_stdin`，也不能直接调用计划执行或回滚。服务断开时插件只显示重连状态，不保留旧 Agent 降级链路。

## 使用

1. 安装依赖：`npm install` 和 `python -m pip install -e apps/local-agent`。
2. 启动本机服务：`python apps/local-agent/src/main.py`。
3. 在 Obsidian 插件设置中配置 OpenAI-compatible 地址、模型和 SecretStorage 密钥。
4. 点击“自动连接”，或等待插件自动发现 `8765-8785` 端口。
5. 打开侧栏后选择会话、Default/Plan 模式和知识范围。

运行数据位于当前 Vault 的 `.obsidian-agent-data/`：`sessions.sqlite3` 保存 Agent 会话，`state.sqlite3` 保存操作状态，`audit.jsonl` 保存写入审计。Skills 默认从 Vault 的 `skills/` 加载。

## 开发

要求 Node.js 20+、Python 3.11+。

```bash
npm run check
npm run test:plugin
npm run test:python
npm run build
```

也可一次运行 `npm run verify`。生产插件文件为 `apps/obsidian-plugin/main.js`、`manifest.json` 和 `styles.css`；开发和测试请使用独立 Vault。
