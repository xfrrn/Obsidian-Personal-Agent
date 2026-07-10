# Operation Plan 第一版实现说明

## 1. 实现范围

本实现将设计文档中的核心安全链路落地为三个部分：

```text
packages/domain
packages/application
adapters/operations
```

当前支持的操作类型与第一版 contracts/domain 保持一致：

```text
create-note
update-note
move-note
update-metadata
create-task
invoke-plugin
```

暂未加入 `rename-note`、`delete-note`、`update-task`，避免在 contracts 和 domain 尚未正式定义前产生多套不一致模型。

---

## 2. 运行链路

```text
OperationPlanBuilder
→ RiskCalculator
→ OperationPlanValidator
→ DefaultPlanPreviewService
→ ConfirmationService
→ ExecutionCoordinator
→ OperationHandlerRegistry
→ Snapshot / Audit / Idempotency
→ RollbackManager
```

### Builder

负责重新计算风险、推断影响资源、设置确认策略、回滚策略、失败策略和计划有效期。

### Validator

执行三类确定性校验：

- 领域结构和完整性哈希
- Handler 是否注册
- 权限、状态、过期时间和前置条件

### ConfirmationService

调用 Domain 的确认状态机，并签发带以下内容的一次性 HMAC Token：

- Plan ID
- Plan Version
- Integrity Hash
- Approver
- Approved Operation IDs
- Nonce
- Expiry

### ExecutionCoordinator

负责：

- Plan 级执行去重
- 一次性令牌消费
- 执行锁
- Operation 级幂等
- 依赖顺序
- Handler 调用
- 快照保存
- 失败策略
- 审计日志
- 回滚协调

---

## 3. 稳定性机制

### 3.1 乐观锁

`update-note`、`move-note` 使用 `expectedVersionHash`；`update-metadata` 使用 `expectedMetadataHash`。执行前哈希不一致即返回冲突，不会覆盖用户的新内容。

### 3.2 原子写入

`LocalFilesystemVault.write_atomic()` 先在同目录写临时文件并执行 `fsync`，再使用 `os.replace()` 原子替换。

### 3.3 双层幂等

- Plan：相同 Plan 只返回原 Execution。
- Operation：相同 `idempotencyKey` 不重复产生副作用。

成功操作被回滚后，其 Operation 幂等记录会删除，允许之后安全重试。

### 3.4 执行锁

提供两种实现：

- `InMemoryExecutionLock`：单进程测试。
- `FileExecutionLock`：本地跨进程互斥参考实现。

### 3.5 补偿回滚

Handler 在 `prepare()` 阶段生成回滚数据；Coordinator 在执行前保存快照。`rollback-all` 会逆序调用已完成操作对应的 Handler。

---

## 4. 持久化适配器

提供：

```text
JsonOperationPlanRepository
JsonExecutionRepository
JsonIdempotencyStore
FileSnapshotStore
JsonlAuditRepository
FileExecutionLock
```

建议运行目录：

```text
.obsidian-agent-data/
├── runtime/
│   ├── plans/
│   ├── executions/
│   ├── idempotency/
│   ├── snapshots/
│   ├── locks/
│   └── audit.jsonl
```

JSON 适配器适合本地第一版和调试。数据规模提升后，可以在不修改 Application/Domain 的情况下替换为 SQLite 或 PostgreSQL 实现。

---

## 5. 运行时版本绑定

同一个 Plan 中，前一个操作可能改变后一个操作需要校验的版本。

例如：

```text
1. update-metadata
2. move-note
```

元数据更新会改变文件完整版本，因此第二步不能继续使用生成 Plan 时的旧版本哈希。

第一版通过 Operation 扩展字段解决：

```python
extensions={
    "expectedVersionFromOperation": "op_update_metadata"
}
```

Coordinator 会在运行时取上游 OperationResult 的 `afterVersionHash`，构造临时执行对象，不修改已确认 Plan，也不影响完整性哈希。

当前支持绑定到：

- `move-note.expectedVersionHash`
- `update-note.expectedVersionHash`

后续可在 contracts v1.1 中将其升级为正式的 `runtimeBindings` 字段。

---

## 6. 文件系统 Handler

### create-note

支持：

- fail
- rename
- overwrite
- merge

回滚时恢复旧文件或删除新文件。

### update-note

支持：

- patch
- replace-section
- replace-content

Patch 会逐段校验 `oldTextHash`。

### move-note

支持：

- 目标冲突检查
- rename 冲突策略
- 基础 WikiLink 和 Markdown Link 更新
- 移动及链接文件整体回滚

正式 Obsidian 插件应优先使用 Obsidian Vault API 和官方链接更新能力；本地 Handler 是 Headless 与端到端测试参考实现。

### update-metadata

使用 PyYAML 解析 Frontmatter，支持：

- set
- remove
- addTags
- removeTags

### create-task

按照 Markdown Task 格式创建或追加任务。

### invoke-plugin

只能调用已经注册的 `pluginId + capability`，不接受任意 JavaScript。插件可选提供 rollback callback。

---

## 7. 权限

默认静态权限名称：

```text
operation.execute
operation.execute.high-risk
operation.execute.critical
note.create
note.update
note.move
task.create
plugin.invoke
```

`StaticPermissionAuthorizer` 是第一版参考实现。后续可以替换为基于 Vault、设备、来源和用户策略的权限引擎。

---

## 8. 测试覆盖

当前测试覆盖：

- 远程写入风险提升
- 保护目录 Critical 风险
- 创建笔记
- Plan 重复执行
- 一次性确认 Token
- 插件调用
- 版本冲突
- rollback-all
- 创建任务
- YAML 元数据修改
- Preview
- JSON Plan 持久化和完整性
- 文件执行锁
- 元数据更新后移动的运行时版本绑定
- Domain 原有状态机和值对象

---

## 9. 正式接入 Obsidian 时的替换点

需要在 TypeScript 插件端实现与 Python Handler 相同的能力：

```text
ObsidianCreateNoteHandler
ObsidianUpdateNoteHandler
ObsidianMoveNoteHandler
ObsidianUpdateMetadataHandler
ObsidianCreateTaskHandler
ObsidianInvokePluginHandler
```

插件端完成写入后，将标准化 `OperationResult` 返回给 Local Agent。Local Agent 继续负责计划、权限、Token、审计和执行状态编排。
