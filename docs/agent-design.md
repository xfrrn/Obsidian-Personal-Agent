# Agent 设计

仓库只有一个 Agent 内核：`apps/local-agent/src/agent/`。它基于 CodeX-Agent 提交 `bdbd148`，继续作为本机 Python 服务运行；Obsidian 插件不直连模型、不做意图分类，也不维护第二套计划循环。

## 运行链路

```text
AssistantView
  -> 带 X-Agent-Token 的会话 HTTP/SSE
  -> CodeX-Agent Session / scheduler / model-tool loop
  -> Obsidian tools + Vault access ledger
  -> build_operation_plan（只暂存预览）
  -> Obsidian 确认或自动策略
  -> OperationManager execute / rollback
  -> 状态数据库与审计日志
```

插件保留原生 DOM、Markdown 渲染、会话和计划面板。Agent 保留流式事件、上下文窗口、自动压缩、Skills、Default/Plan Mode、权限、审批和 SQLite 会话持久化。

## 工具边界

默认保留 `current_time`、上下文窗口工具和 `update_plan`，并注入知识库业务工具：

- `search_notes`、`read_note`、`list_tasks`
- `inspect_note`、`analyze_project`、`check_vault_health`
- `list_tags`、`find_related_notes`、`find_duplicates`
- `list_rules`、`evaluate_rules`、`extract_task_candidates`
- `build_operation_plan`

不注册 `apply_patch`、`exec_command`、`write_stdin`、OperationPlan 执行或回滚工具。Default Mode 可以生成修改预览；Plan Mode 对 Vault 写入硬失败，只输出实现计划。

每回合的 `ToolExecutionContext` 包含 session、submission、模式、scope、当前笔记、选中文本和插件验证过的引用路径。访问账本再记录本回合搜索或读取返回的现有笔记；OperationPlan 只能修改账本内路径。新建路径仍由 Vault 安全相对路径校验负责。

## 协议

首次 `/handshake` 绑定一个含 `.obsidian` 的 Vault 并返回随机令牌。除 `/health` 与首次握手外，接口要求 `X-Agent-Token`。

```text
GET/POST /api/sessions
GET      /api/sessions/{id}
POST     /api/sessions/{id}/archive
POST     /api/sessions/{id}/messages/stream
POST     /api/sessions/{id}/approvals
```

SSE 转发 `turn_started`、`assistant_message`、`tool_call`、`tool_result`、`plan_updated`、审批和终止事件，并在 `build_operation_plan` 成功后追加结构化 `operation_plan`。新消息会中断同会话旧回合，不影响其他会话。

## 写入安全

- 模型只能生成并暂存计划，执行和回滚只能由插件界面请求。
- 现有路径必须属于当前回合访问账本；禁止绝对路径、`..`、协议路径和受保护目录。
- 一次最多 10 个操作；不永久删除笔记；只删除空目录。
- 执行前校验计划完整性、有效期和预览时文件哈希。
- 写入串行执行；失败反向回滚；回滚前再次检查文件状态。
- `.obsidian-agent-data/state.sqlite3` 保存操作状态，`audit.jsonl` 保存审计。
- `.obsidian-agent-data/sessions.sqlite3` 只保存会话；API Key 不进入数据库或日志。
