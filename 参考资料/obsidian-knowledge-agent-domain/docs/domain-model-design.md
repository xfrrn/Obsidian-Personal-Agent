# packages/domain 领域模型设计文档

## 1. 文档目标

本文定义个人知识库 Agent 的核心领域模型，覆盖笔记、任务、项目、标签和操作计划。该模块位于系统最内层，负责表达业务概念、状态转换、约束和领域事件，不负责 HTTP、数据库、文件系统、大模型调用或 Obsidian 界面。

项目路径：

```text
packages/domain/
```

Python 导入包：

```python
import oka_domain
```

## 2. 领域层职责

领域层负责：

- 表达知识库中的核心对象及其关系；
- 约束合法状态和状态转换；
- 计算内容、元数据和计划完整性哈希；
- 产生领域事件；
- 定义仓储和 Unit of Work 接口；
- 校验操作计划的依赖、风险、确认和过期规则。

领域层不负责：

- 将 Markdown 解析成领域模型；
- 将领域模型写回 Obsidian Vault；
- 将 JSON Schema 转换为 Python 对象；
- 执行 OperationPlan；
- 调用 LLM、Embedding、数据库或其他插件。

这些职责分别属于适配器层、应用层和执行器。

## 3. 依赖规则

```text
apps / adapters / application
              ↓
        packages/domain
```

`packages/domain` 只允许依赖 Python 标准库，不能反向依赖：

```text
FastAPI
Pydantic
SQLAlchemy
Obsidian API
OpenAI SDK
packages/contracts 生成代码
```

`contracts` 是传输模型，`domain` 是业务模型。二者通过应用层 Mapper 转换。

## 4. 目录结构

```text
packages/domain/
├── src/oka_domain/
│   ├── common/
│   │   ├── aggregate.py
│   │   ├── events.py
│   │   ├── hashing.py
│   │   ├── repositories.py
│   │   ├── resources.py
│   │   └── value_objects.py
│   ├── notes/
│   ├── tasks/
│   ├── projects/
│   ├── tags/
│   ├── operations/
│   └── exceptions.py
├── tests/
├── pyproject.toml
└── README.md
```

每个领域模块按以下方式划分：

```text
models.py         聚合根、实体和值对象
 events.py        领域事件
repositories.py   仓储 Port
__init__.py       对外稳定导出
```

## 5. 通用模型

### 5.1 标识符

系统使用强类型标识：

- `NoteId`
- `TaskId`
- `ProjectId`
- `TagId`
- `OperationId`
- `PlanId`

它们底层仍然是字符串，但能够避免把任务 ID 误传给笔记仓储。

### 5.2 VaultPath

`VaultPath` 统一约束知识库相对路径：

- 只能使用 `/`；
- 不能以 `/` 开头；
- 不能包含 `..`；
- 不能包含反斜杠；
- 领域中的笔记路径必须以 `.md` 结尾。

路径是否实际存在由仓储或执行器检查。

### 5.3 哈希

系统使用三个笔记哈希：

```text
content_hash   正文内容哈希
metadata_hash  属性、标签和状态哈希
version_hash   路径、标题、正文哈希和元数据哈希的综合版本
```

它们用于乐观并发控制。Agent 生成计划后，如果用户手动修改了文件，执行器应拒绝旧计划。

### 5.4 AggregateRoot

`Note`、`Task`、`Project`、`Tag` 和 `OperationPlan` 都是聚合根。

聚合根具有：

- `revision`：领域状态修订号；
- `_domain_events`：尚未发布的事件；
- `pull_domain_events()`：应用层提交后读取并清空事件。

## 6. Note 聚合

### 6.1 主要字段

```text
note_id
path
title
body
metadata
tags
links
headings
status
created_at
updated_at
content_hash
metadata_hash
version_hash
revision
```

### 6.2 NoteMetadata

系统字段与扩展字段分离：

```text
note_type
project_id
status
properties
```

`properties` 用于保存用户自定义 Frontmatter，不把所有 YAML 字段硬编码到领域模型中。

### 6.3 关键操作

```text
rename()
move_to()
replace_body()
update_metadata()
add_tags()
remove_tags()
archive()
mark_trashed()
```

不允许应用层直接赋值来绕过事件和校验。

### 6.4 领域事件

```text
note.created
note.content-changed
note.moved
note.metadata-changed
note.archived
```

索引、知识图谱、健康检查等模块订阅这些事件，而不是和 Note 代码直接耦合。

## 7. Task 聚合

### 7.1 状态

```text
todo
doing
blocked
done
cancelled
```

任务状态转换：

```text
todo ─────→ doing ─────→ done
 │           │             │
 ├────────→ blocked ───────┘
 └────────→ cancelled

done / cancelled / blocked ─→ todo（reopen）
```

### 7.2 关键约束

- 标题不能为空；
- 任务不能依赖自己；
- 任务不能把自己设置为父任务；
- `blocked` 必须有 `blocked_reason`；
- `done` 必须有 `completed_at`；
- 已完成任务不能直接取消，需要先 reopen。

### 7.3 与笔记和项目的关系

Task 只保存引用：

```text
project_id
source_note_id
source_note_path
target_path
```

它不持有完整 Project 或 Note，避免形成巨大聚合和循环加载。

## 8. Project 聚合

### 8.1 状态

```text
planned
active
on-hold
completed
archived
cancelled
```

标准生命周期：

```text
planned → active ↔ on-hold → completed → archived
   └──────────────────────────────→ cancelled
```

### 8.2 文档槽位

Project 不直接持有所有笔记，而是维护关键文档槽位：

```text
overview
requirements
design
tasks
decisions
issues
changelog
readme
```

自定义角色也可以通过字符串注册，因此不会限制后续扩展。

`missing_standard_documents()` 可用于项目完整性检查。

### 8.3 里程碑

`ProjectMilestone` 是 Project 聚合内部实体，不能绕过 Project 单独保存。这样完成里程碑时能够同时产生项目领域事件。

## 9. Tag 聚合

### 9.1 分类

```text
type
 domain
project
status
source
custom
```

### 9.2 状态

```text
active
deprecated
merged
```

### 9.3 标签规范

`TagName` 会：

- 去掉开头 `#`；
- 将空格转换为 `-`；
- 合并重复 `/` 和 `-`；
- 对英文执行大小写归一化；
- 拒绝 Obsidian 标签中不适合使用的字符。

标签实体支持：

```text
add_alias()
deprecate()
merge_into()
```

合并标签不会立即修改全库笔记。应用层应根据 TagMerged 事件创建批量 `UpdateMetadataOperation`。

## 10. OperationPlan 聚合

OperationPlan 是系统所有写入行为的统一安全边界。

### 10.1 当前支持的操作

与第一版 contracts 对齐：

```text
create-note
update-note
move-note
update-metadata
create-task
invoke-plugin
```

### 10.2 领域模型与 contracts 字段映射

| Domain | Contract |
|---|---|
| `operation_type` | `type` |
| `set_values` | `set` |
| `remove_keys` | `remove` |
| `risk_level.contract_value` | `riskLevel` |
| `integrity` | `integrity.value` |
| `Sha256Hash` | 64 位十六进制字符串 |
| `VaultPath` | Vault 相对路径字符串 |

领域层不直接实现 `to_contract()`，以免依赖通信协议。Mapper 放在：

```text
packages/application/mappers/
```

### 10.3 计划结构约束

OperationPlan 会验证：

1. 至少包含一个操作；
2. 操作 ID 唯一；
3. 操作顺序唯一；
4. 所有依赖都存在；
5. 依赖操作必须排在被依赖操作之前；
6. 依赖图不能形成环；
7. Plan 风险等级不能低于最高风险操作；
8. Critical 计划必须确认；
9. 需要确认时必须配置 ConfirmationPolicy；
10. `expires_at` 必须晚于 `created_at`；
11. 计划内容必须匹配完整性哈希。

### 10.4 风险等级

领域内部使用可比较的 `IntEnum`：

```text
LOW = 1
MEDIUM = 2
HIGH = 3
CRITICAL = 4
```

传输时转换为：

```text
low
medium
high
critical
```

### 10.5 确认流程

```text
创建计划
  ↓
低风险：confirmed
中高风险：pending-confirmation
  ↓
confirm()
  ↓
confirmed
  ↓
start_execution()
  ↓
executing
  ├─ mark_completed()
  └─ mark_failed()
        ↓
   mark_rolled_back()
```

`SELECTED_OPERATIONS` 模式下，选择某个操作时必须同时选择其全部依赖。

### 10.6 完整性哈希

计划完整性哈希用于检测：

- Agent 返回后计划被篡改；
- 确认页面和执行页面使用了不同计划；
- 传输过程中字段丢失或变化。

计划完整性覆盖 Agent 提出的不可变内容，但不包含 `status`、`approval`、`revision` 和 `integrity` 本身。确认和执行只改变生命周期状态，不需要重签原计划。当前模型提供 `calculate_integrity()` 和 `validate_integrity()`，执行器必须在执行前调用。

## 11. 仓储接口

领域包只定义 Port：

```text
NoteRepository
TaskRepository
ProjectRepository
TagRepository
OperationPlanRepository
UnitOfWork
```

具体实现可能是：

```text
ObsidianVaultNoteRepository
SQLiteTaskRepository
SQLiteOperationPlanRepository
InMemoryRepository（测试）
```

仓储应负责乐观锁。保存时建议携带加载时的 revision 或 version_hash；发生冲突时抛出 `ConcurrencyConflict`。

## 12. 聚合边界

### 12.1 为什么 Project 不包含全部 Note 和 Task

大型项目可能有上千条笔记和任务。如果 Project 聚合直接持有全部对象：

- 每次更新项目都要加载大量数据；
- 容易出现并发冲突；
- 无法独立索引；
- 远程查询成本过高。

因此 Project 只维护自身状态、关键文档槽位和里程碑。项目全貌由 Application 层通过多个 Repository 组合生成。

### 12.2 为什么 Note 不直接持有 Tag 实体

Note 使用 `TagName`，Tag Registry 使用 `Tag` 聚合。这样 Markdown 可直接保存标签文本，同时标签中心仍能维护别名、废弃和合并关系。

## 13. 一致性策略

### 聚合内强一致

单个 Note、Task、Project、Tag 或 OperationPlan 的状态由自身方法立即校验。

### 聚合间最终一致

例如：

```text
TagMerged
  → 应用层订阅事件
  → 查询使用旧标签的笔记
  → 生成 OperationPlan
  → 用户确认
  → 批量更新 Markdown
```

不会在 Tag.merge_into() 中直接修改所有笔记。

## 14. 应用层调用示例

```python
from oka_domain.common import TagName, VaultPath
from oka_domain.notes import Note

note = Note.create(
    path=VaultPath("Inbox/知识库Agent.md"),
    title="知识库 Agent",
    body="初始内容",
)

note.add_tags(TagName("Agent"), TagName("Obsidian"))
note.replace_body("整理后的内容", expected_content_hash=note.content_hash)

events = note.pull_domain_events()
```

应用层流程：

```text
读取仓储
→ 调用聚合方法
→ 保存聚合
→ 提交 UnitOfWork
→ 发布领域事件
```

## 15. 与后续模块的衔接

### packages/application

负责：

- Use Case；
- contracts ↔ domain Mapper；
- 多仓储编排；
- 权限检查；
- OperationPlan 执行流程。

### adapters/filesystem

负责：

- Markdown 解析和生成；
- Obsidian Vault 文件读写；
- 文件哈希和快照；
- 链接更新。

### knowledge-engine

读取 Note 快照构建索引，不修改领域对象。

### rule-engine

输出分类、标签和归档建议，再由 Application 转成领域操作计划。

## 16. 第一版明确不包含

当前领域包暂不加入：

- 用户和多租户聚合；
- 知识图谱实体与关系聚合；
- 自动化计划聚合；
- 附件完整聚合；
- 对话历史聚合；
- 执行结果和回滚快照聚合；
- 任务循环规则；
- 项目成员协作模型。

这些能力将在实际需求明确后独立扩展，避免第一版过度设计。

## 17. 测试重点

当前测试覆盖：

- 路径遍历拒绝；
- 标签标准化；
- 笔记哈希、revision 和事件；
- 任务状态机；
- 项目生命周期与文档槽位；
- 标签别名和废弃；
- 操作计划确认和执行；
- 错误依赖顺序拒绝。

后续应继续补充：

- 仓储乐观锁集成测试；
- Contract Mapper 双向测试；
- OperationPlan JSON Schema 一致性测试；
- 批量操作失败和回滚测试；
- 时区与过期边界测试。
