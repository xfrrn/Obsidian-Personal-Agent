# Agent 设计

本文基于项目结构设计文档，定义本项目 Agent 的长期边界和第一阶段落点。仓库已铺开 `apps/local-agent`、`packages/*`、`adapters/*` 等结构；当前可运行能力仍先落在 Obsidian 插件内，后续逐步把推理、工具选择和检索迁到 `apps/local-agent`。

## 核心原则

Agent 不直接改文件。所有写操作必须先生成 `OperationPlan`，展示给用户确认，再由执行器校验并执行。

```text
用户输入
  -> 意图判断
  -> 检索上下文
  -> 问答或生成操作计划
  -> 计划校验
  -> 用户确认
  -> 执行器写入 Vault
  -> 回滚/审计日志
```

## 第一阶段运行边界

第一阶段只有一个真正运行组件：Obsidian 插件。其他目录已作为架构边界铺好，但先不接入运行链路。

插件负责：

- 显示 Agent 对话视图。
- 读取当前笔记、Vault 目录和候选笔记内容。
- 调用 OpenAI-compatible 模型。
- 校验模型返回的 JSON、路径和引用。
- 生成并展示修改计划。
- 用户确认后通过 Obsidian API 执行计划。
- 写入审计日志并在失败时回滚已执行步骤。

插件后续会移出的职责：

- 独立本地 Agent 服务。
- 向量索引、重排、知识图谱。
- 外部通讯入口。
- Headless 执行器。
- 多模型供应商管理。

已铺开的长期结构：

```text
apps/
  obsidian-plugin/   当前运行插件
  local-agent/       后续承接 Agent Runtime
  gateway/           外部通讯入口
  admin-web/         管理后台
packages/
  contracts/         通信协议
  domain/            领域模型
  application/       业务用例
  agent-core/        Agent 对话、意图、规划、工具
  rule-engine/       确定性规则
  knowledge-engine/  解析、索引、检索
  graph-engine/      知识图谱
  integration-sdk/   扩展接口
  shared/            共享工具
adapters/            LLM、Embedding、存储、文件系统和插件适配器
```

## 当前代码映射

```text
apps/obsidian-plugin/src/
  features/assistant/
    agent-loop.ts      意图判断和问答调度
    prompts.ts         第一阶段提示词
    types.ts           查询范围
  features/knowledge-search/
    search-notes.ts    候选笔记选择
  features/operation-preview/
    operation-plan.ts      操作计划协议和校验
    operation-executor.ts  计划生成、执行、回滚、审计
  obsidian/
    vault-reader.ts    当前笔记、Vault 目录、上下文读取
  utils/
    protocol.ts        模型响应解析和安全校验
  views/assistant-view/
    assistant-view.ts  Agent UI、计划预览和确认入口
```

## Agent 运行时

第一阶段的 Agent Runtime 是 `agent-loop.ts` 和 `operation-executor.ts` 组合出来的轻量流程。

```text
AssistantView
  -> plugin.intent / ask / plan / executePlan
  -> agent-loop.ts 或 operation-executor.ts
  -> model-client.ts
  -> protocol.ts / operation-plan.ts
  -> vault-reader.ts / Obsidian Vault API
```

运行时只做四件事：

1. 判断用户是问答还是修改请求。
2. 为只读问答选择工具。
3. 给模型提供受控上下文，并解析结构化结果。
4. 把写操作收敛到 `OperationPlan`。

## Operation Plan 合同

第一阶段继续沿用 TypeScript 内部合同，不急着拆 `packages/contracts`。

```typescript
type KnowledgeOperation =
  | { type: "create-note"; path: string; content: string }
  | { type: "update-note"; path: string; oldText: string; newText: string }
  | { type: "move-note"; path: string; targetPath: string }
  | { type: "update-metadata"; path: string; set?: object; remove?: string[] }
  | { type: "create-task"; path: string; title: string }
  | { type: "invoke-plugin"; commandId: string };
```

执行规则：

- 一次最多 10 个操作。
- 不支持删除。
- `update-note` 只能修改本次上下文中的笔记。
- `oldText` 必须逐字匹配且唯一。
- 插件命令必须是最后一步。
- 路径只能是 Vault 内相对 Markdown 路径。

后续拆出 local-agent 时，把这份合同迁到 `packages/contracts`，并补齐 `schemaVersion`、`planId`、`createdAt`、`expectedHash`。

## 工具边界

Local Agent 已接入统一 Tool Registry。只读分析 tool 直接调用 Application use case；所有写操作仍统一进入 OperationPlan。

| 工具 | 当前实现 | 风险 |
| --- | --- | --- |
| `search_notes` / `read_note` / `list_tasks` | Local Vault repository | low |
| `inspect_note` / `analyze_project` | Application 组合用例 | low |
| `check_vault_health` / `list_tags` | 确定性全库分析 | low |
| `find_related_notes` / `find_duplicates` | 链接、标签和词项分析 | low |
| `list_rules` / `evaluate_rules` | 内置规则和 Vault 规则覆盖 | low |
| `extract_task_candidates` | 本地启发式候选提取 | low |
| `build_operation_plan` | 持久化计划与动态风险 | low / prepare-write |
| `execute_operation_plan` | 真实文件执行器 | high / system-only |
| `rollback_operation` | 版本校验后撤销 | high / system-only |

所有工具定义都包含 `input_schema`、`output_schema`、`permission`、`risk_level` 和调用策略；P3 外部能力暂不注册。

## 安全设计

第一阶段必须保留这些硬约束：

- 模型输出只接受 JSON 对象。
- 所有引用路径必须来自真实 Vault 路径。
- 写入路径禁止绝对路径、协议路径、`..` 和 `.obsidian`。
- 修改正文前再次校验 `oldText`。
- 执行过程串行化，避免多个计划同时写入。
- 执行失败后按已完成步骤反向回滚。
- 审计日志写入 `.obsidian-agent-data/audit.jsonl`。

下一步最值得补的是 `expectedHash`，它比只校验 `oldText` 更能发现用户在预览后改过文件。

## 后续迁移方向

仓库结构已经铺好，运行链路按下面顺序接入：

1. `packages/contracts`：沉淀 Operation Plan、Agent Response、Handshake schema。
2. `apps/local-agent`：承接意图判断、上下文构建、计划生成。
3. `packages/knowledge-engine`：把全库扫描替换成索引和检索。
4. `packages/rule-engine`：确定性分类、命名、标签和归档规则优先于 AI。
5. `adapters/llm`：把 OpenAI-compatible 调用从插件中移出。

gateway、admin-web、graph-engine 和 messaging adapters 先只保留骨架；等有真实远程入口或图谱需求再接入运行链路。
