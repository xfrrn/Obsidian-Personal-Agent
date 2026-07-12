# Agent Tools 开发规划

更新时间：2026-07-12

当前进度：P0 代码已完成，等待 Obsidian 测试 Vault 人工验收。

## 一、规划结论

本项目只维护一套由 Local Agent 托管的 Tool Registry，不为每项业务能力创建独立 Obsidian 插件。

开发原则：

1. Agent 只能通过 tool 获取外部信息或触发系统行为。
2. 所有 Vault 写入都先生成 `OperationPlan`，再由统一执行器执行。
3. 创建、更新、移动笔记或任务属于 Operation，不再注册一组可绕过计划的直接写入 tools。
4. 摘要、对比、改写、生成草稿属于模型能力，复用检索和读取 tools，不单独注册同名 tool。
5. Tasks、Dataview、Templater 等属于 adapter，复用统一 tool 名，不把第三方插件名暴露成业务接口。
6. 当前主要供个人使用，先不开发多租户、角色管理和工具市场。

## 二、当前基线

当前 Local Agent 已注册 6 个 tools：

| Tool | 当前状态 | 主要缺口 |
| --- | --- | --- |
| `search_notes` | 可用 | 缺少时间、标签、项目等结构化过滤 |
| `read_note` | 可用 | 只能读取单篇笔记 |
| `list_tasks` | 可用 | 需要补齐 Tasks 插件日期、优先级、重复任务语法 |
| `build_operation_plan` | 可用 | 已持久化计划、完整性哈希、动态风险和有效期 |
| `execute_operation_plan` | 可用 | 已接入真实文件执行、确认令牌、冲突检测、审计和失败回滚 |
| `rollback_operation` | 可用 | 已支持执行后显式撤销和版本冲突保护 |

现有 `ToolDefinition` 已包含权限、风险、影响类型、调用策略和确认标记；现有 `ToolRegistry` 已能在执行边界拒绝越权调用。这些结构继续复用，不再增加第二套工具框架。

## 三、需求等级

这里的等级表示开发顺序，不表示其他需求被取消。

| 等级 | 定义 | 目标 |
| --- | --- | --- |
| P0 必须 | 没有它就不能安全使用 | 打通真实、安全、可撤销的本地执行链路 |
| P1 高频 | 个人日常会频繁使用 | 完成笔记、任务、项目的查询与整理闭环 |
| P2 增强 | 定期使用，能显著提高知识库质量 | 规则、标签、链接、健康检查和关系发现 |
| P3 扩展 | 依赖真实使用场景再接入 | 自动化、外部输入、通讯入口、第三方插件和知识图谱 |

## 四、Tool 清单与优先级

### P0：执行与安全基础

| Tool | 类型 | 风险 | 处理方式 |
| --- | --- | --- | --- |
| `search_notes` | read | low | 保留现有实现 |
| `read_note` | read | low | 保留现有实现 |
| `list_tasks` | read | low | 保留现有实现 |
| `build_operation_plan` | prepare_write | low | 补齐真实操作规划、动态风险和持久化 |
| `execute_operation_plan` | write / system-only | 动态 | 接入真实执行器、确认令牌、冲突校验、审计和回滚 |
| `rollback_operation` | write / system-only | high | 撤销已完成操作；要求本地确认并校验当前文件版本 |

P0 不增加业务型写入 tool。`create_note`、`update_note`、`move_note`、`update_metadata`、`create_task` 和 `invoke_plugin` 继续作为 `OperationPlan` 内部操作。

完成标准：

- Local Agent 不再使用 Noop 写入执行器。
- 计划重启后仍可查询、确认或判定过期。
- 预览后文件发生变化时拒绝执行。
- 成功、失败、回滚都有审计记录。
- 三种权限模式均在执行边界生效，而不是只由 UI 控制。

### P1：个人日常核心能力

| Tool | 用途 | 实现建议 |
| --- | --- | --- |
| `inspect_note` | 一次完成规范校验、类型判断、链接检查、归档建议和缺失项分析 | 合并现有 `validate_note`、`classify_note` 占位能力 |
| `analyze_project` | 汇总项目笔记、任务、状态、缺失文档和下一步 | 复用笔记与任务 repository，不复制检索实现 |

同时增强现有 tools：

- `search_notes`：增加 `path`、`tags`、`project`、`modified_after`、`modified_before` 和排序参数。
- `read_note`：增加受控批量读取参数，限制篇数和总字符数，返回真实引用。
- `list_tasks`：兼容已安装的 Tasks 插件格式，支持状态、日期、优先级、项目和来源笔记过滤。
- `build_operation_plan`：支持任务更新、项目建档、当前笔记整理和单文件移动。

以下需求不新增 tool：

- 笔记摘要、对比、润色、扩写、周报和专题笔记：模型调用 `search_notes`、`read_note` 后生成结果。
- 创建或更新任务：生成 OperationPlan，由执行器修改 Markdown/Tasks 语法。
- Tasks 插件集成：作为 task repository adapter，不增加 `tasks_plugin_*` tools。

完成标准：

- 能回答跨多篇笔记的问题并提供真实段落引用。
- 能查询今日、本周、逾期及指定项目任务。
- 能输出项目总览、缺失内容和下一步行动。
- 能检查当前笔记并生成可确认的整理计划。

### P2：知识库治理与关系发现

| Tool | 用途 | 风险 |
| --- | --- | --- |
| `check_vault_health` | 检查 YAML、命名、无标签、失效链接、孤立笔记和未归档项目 | low，只读报告 |
| `list_tags` | 返回标签、别名、分类、使用次数和废弃状态 | low |
| `find_related_notes` | 基于链接、元数据和检索结果推荐相关笔记 | low |
| `find_duplicates` | 找出重复文件名和高相似内容 | low |
| `evaluate_rules` | 对指定笔记或目录试运行确定性规则 | low，只生成建议 |
| `list_rules` | 查看当前目录、命名、标签、项目和自动化规则 | low |
| `extract_task_candidates` | 从正文中提取潜在任务和来源 | low，只生成候选 |

规则修改、标签迁移、补链接和批量整理仍统一生成 OperationPlan。先做确定性检查；只有分类、相似度或内容理解无法由规则可靠完成时才调用模型。

完成标准：

- 可生成全库健康报告，并定位到具体文件和问题。
- 新标签默认只能推荐，加入正式标签体系需要确认。
- 关系推荐区分确定链接、元数据关系和 AI 推测。
- 批量修改始终展示影响文件数和差异。

### P3：自动化、输入与外部集成

| Tool | 用途 | 默认策略 |
| --- | --- | --- |
| `list_automations` | 查看定时任务、授权范围和最近运行结果 | read / low |
| `fetch_url` | 获取指定网页正文用于入库 | network / medium，限制协议、大小和重定向 |
| `extract_document` | 提取 PDF、Word、字幕等受支持文件内容 | read / medium，限制允许目录和文件大小 |
| `list_plugin_capabilities` | 返回已登记的 Obsidian 插件能力白名单 | read / low |
| `query_graph` | 查询已确认的知识关系和路径 | read / low |

边界说明：

- Scheduler、外部消息接收和文件监听是 Runtime Trigger，不注册为 tool。
- Telegram、飞书、邮件等入口属于 Gateway Adapter，不为每个渠道复制一套业务 tools。
- 网页、PDF、Word、图片和语音先统一转换成 `KnowledgeInput`，后续分类和写入继续复用现有 tools。
- 插件调用和发送通知属于有副作用的 Operation，由 `execute_operation_plan` 执行，不开放直接调用 tool。
- Dataview、Templater、Kanban 等只有在实际安装并出现稳定需求后才增加 adapter。

完成标准：

- 自动化任务只能在用户预授权的目录、操作类型和数量上限内运行。
- 外部来源默认只读；写入只能生成计划，高风险操作必须回到本地确认。
- 第三方插件只能调用登记过的 capability，不能执行任意 JavaScript 或任意命令 ID。

## 五、三种权限模式

### 1. 全部确认 `confirm_all`

- 所有读取自动执行。
- 所有 Vault 写入都展示计划和差异，并由用户确认。
- 适合作为首次启用和新 tool 上线后的默认模式。

### 2. 风险分级 `risk_based`

- 读取自动执行。
- 用户预授权的 low 风险写入可自动执行。
- medium 和 high 风险必须确认。
- 适合作为日常默认模式。

### 3. 无人值守 `unattended`

- 定时任务和规则触发器可以自动运行。
- 仅允许预授权、可回滚、幂等的 low 风险写入自动执行。
- medium 风险生成待确认计划；high 风险只能在本地显式确认。
- 删除、保护目录、批量移动、任意插件调用和外部副作用永不自动执行。

权限矩阵：

| 操作 | `confirm_all` | `risk_based` | `unattended` |
| --- | --- | --- | --- |
| 只读查询 | 自动 | 自动 | 自动 |
| 生成建议或计划 | 自动 | 自动 | 自动 |
| low 写入 | 确认 | 白名单内自动 | 白名单且由可信触发器发起时自动 |
| medium 写入 | 确认 | 确认 | 排队等待确认 |
| high 写入 | 本地明确确认 | 本地明确确认 | 本地明确确认 |
| 外部来源写入 | 本地确认 | 本地确认 | 只生成待确认计划 |

最小配置只需要：

```yaml
execution_mode: risk_based
auto_allow:
  - note.create.inbox
  - task.create.pending
  - metadata.touch_updated_at
protected_paths:
  - .obsidian
  - .git
max_auto_affected_files: 5
```

`execute_operation_plan` 仍保持 system-only。自动模式不能伪造普通用户确认，而应生成与 `planId`、完整性哈希、授权规则和有效期绑定的系统预授权。

## 六、风险计算

Tool 的静态风险只是基础值，OperationPlan 的最终风险由执行器重新计算：

```text
最终风险 = 操作类型基础风险
         + 影响文件数量和正文规模
         + 请求来源
         + 目标目录保护级别
         + 是否可回滚
         + 是否包含第三方插件或外部副作用
```

建议规则：

- 创建 Inbox 笔记、追加待确认任务、更新时间：low。
- 修改单篇 YAML、任务状态、少量正文或移动单文件：medium。
- 批量移动、批量标签迁移、大段正文替换、插件写操作：high。
- 删除、保护目录修改和远程危险操作：high 且强制两步本地确认；引入永久删除时再增加独立 critical 风险级别。

## 七、实施顺序

### 里程碑 M0：完成已有 tools 的闭环（代码已完成）

1. 接入真实 Local Agent OperationPlan 执行器。
2. 把计划存储从内存改为本地持久化。
3. 实现三种权限模式和动态风险计算。
4. 补充用户主动撤销和执行历史。
5. 用测试 Vault 完成写入、冲突、失败回滚和审计验收。

### 里程碑 M1：日常可用

1. 增加 `inspect_note`、`analyze_project`。
2. 增强 `search_notes`、`read_note` 和 `list_tasks`，接入 Tasks adapter。
3. 让 Planner 能进行“搜索 → 读取 → 分析 → 生成计划”的受限多轮工具调用。

### 里程碑 M2：治理能力

1. 增加健康检查、标签统计、重复检测和关联推荐。
2. 增加规则读取与试运行。
3. 再开放受控批量 OperationPlan。

### 里程碑 M3：自动化与外部能力

1. 先实现本地定时任务和待确认队列。
2. 再接网页和文档输入。
3. 最后接外部通讯、第三方插件 capability 和知识图谱查询。

## 八、下一批开发任务

先在测试 Vault 完成 M0 人工验收：

- 分别验证创建、替换、移动、Frontmatter、任务操作。
- 验证三种权限模式、预览后冲突拒绝、失败回滚和显式撤销。
- 检查 SQLite 状态和 JSONL 审计记录。

验收通过后，再开始增强 `read_note`、实现 `inspect_note` 和 Tasks adapter。
