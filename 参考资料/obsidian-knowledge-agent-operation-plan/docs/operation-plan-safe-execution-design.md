# Operation Plan 安全执行协议设计文档

## 1. 文档信息

- 文档名称：Operation Plan 安全执行协议设计文档
- 所属项目：Obsidian Knowledge Agent
- 所属模块：`packages/application` / `packages/domain` / `packages/contracts`
- 当前版本：v1.0
- 状态：设计定稿
- 适用范围：
  - Obsidian 插件内的自动修改
  - 本地 Agent 生成的修改计划
  - 定时自动化任务
  - 外部通讯软件下发的远程指令
  - 其他插件能力调用
  - 后续 Headless 执行器

---

# 2. 设计目标

Operation Plan 是整个系统所有写操作的统一安全边界。

系统中的任何自动修改，无论来源于用户自然语言指令、AI 推理、规则引擎、定时任务、外部通讯软件还是其他插件，都不得绕过 Operation Plan 直接修改知识库。

Operation Plan 需要保证：

1. 所有写操作在执行前都经过结构化描述。
2. 所有修改都可以预览。
3. 所有高风险修改都必须确认。
4. 所有修改都要经过版本与前置条件校验。
5. 所有修改都必须具备幂等性。
6. 所有执行过程都可以审计。
7. 所有可恢复修改都应支持回滚。
8. 单项失败不能导致知识库处于不可解释状态。
9. 插件断开、Agent 崩溃或重复请求不能造成重复写入。
10. 外部远程指令不能获得超过授权范围的执行能力。

---

# 3. 非目标

Operation Plan 不负责：

- 判断用户真正想做什么。
- 进行自然语言意图识别。
- 决定笔记应该如何分类。
- 生成正文内容。
- 决定任务优先级。
- 实现具体数据库访问。
- 直接调用大模型。
- 直接操作 Obsidian Vault。
- 代替 Git、同步软件或文件系统备份。

这些工作分别由 Agent Core、Rule Engine、Application Use Case、Operation Executor 和基础设施适配器负责。

Operation Plan 只描述：

> 要修改什么、为什么修改、修改前必须满足什么、风险有多高、如何执行、失败后如何处理，以及如何恢复。

---

# 4. 核心原则

## 4.1 所有写操作计划化

禁止以下形式：

```python
await vault.write(path, content)
```

允许的形式：

```python
plan = build_operation_plan(...)
await operation_plan_repository.save(plan)
```

真正写入必须由执行器完成：

```python
await executor.execute(plan_id, confirmation)
```

---

## 4.2 Agent 只有规划权，没有直接写权限

Agent 可以：

- 查询知识库。
- 分析上下文。
- 生成修改建议。
- 生成 Operation Plan。
- 解释计划。
- 根据冲突重新规划。

Agent 不可以：

- 直接覆盖文件。
- 直接删除文件。
- 绕过确认。
- 修改执行结果。
- 伪造用户确认。
- 将自然语言指令直接传给执行器。

---

## 4.3 确定性校验优先于 AI 判断

执行前的校验必须由程序完成，包括：

- 文件是否存在。
- 文件版本是否一致。
- 目标路径是否冲突。
- 权限是否足够。
- 操作依赖是否合法。
- 计划是否过期。
- 是否已经执行。
- 确认令牌是否有效。
- 插件能力是否可用。

这些判断不得交给大模型。

---

## 4.4 默认拒绝不确定执行

遇到以下情况时必须停止：

- 文件版本变化。
- 目标路径已存在。
- 操作类型未知。
- 计划结构不完整。
- 权限不足。
- Critical 操作没有显式确认。
- 回滚数据缺失。
- 插件能力不可用。
- 执行器无法确认操作是否已经执行。

系统应返回明确错误，而不是自动猜测。

---

## 4.5 执行过程不可变

计划进入 `confirmed` 状态后，其内容必须不可修改。

任何调整都必须：

1. 创建新的 `planVersion`。
2. 重新计算完整性哈希。
3. 重新进行风险评估。
4. 重新获取确认。

---

# 5. 系统边界

```text
用户或外部系统
        │
        ▼
Agent / Rule Engine / Use Case
        │
        ▼
Operation Plan Builder
        │
        ▼
Operation Plan Validator
        │
        ▼
Operation Plan Repository
        │
        ▼
Preview / Confirmation
        │
        ▼
Operation Executor
        │
        ├── Obsidian Executor
        ├── Headless Executor
        └── Plugin Capability Executor
        │
        ▼
Execution Result / Audit / Rollback
```

---

# 6. 核心术语

## 6.1 Operation Plan

一次完整写操作意图的结构化计划。

它可以包含一个或多个相互依赖的操作。

示例：

```text
把临时笔记移动到 AutoUp 项目目录，并补充项目标签。
```

可能生成：

```text
1. 更新笔记元数据
2. 移动笔记
3. 调用 Dataview 刷新
```

---

## 6.2 Knowledge Operation

计划中的最小可执行单元。

第一版支持：

- `create-note`
- `update-note`
- `move-note`
- `rename-note`
- `update-metadata`
- `delete-note`
- `create-task`
- `update-task`
- `invoke-plugin`

---

## 6.3 Execution

一次对已确认计划的实际执行。

同一个 Operation Plan 原则上只允许产生一次成功执行。

---

## 6.4 Rollback

根据执行前快照或逆向操作，将已执行修改恢复到执行前状态。

---

## 6.5 Precondition

某项操作执行前必须满足的确定性条件。

---

## 6.6 Postcondition

某项操作执行后必须满足的结果条件。

---

## 6.7 Idempotency Key

用于防止重复请求导致重复写入的稳定标识。

---

## 6.8 Integrity Hash

对计划中不可变字段计算的哈希，用于防止计划确认后被篡改。

---

# 7. 聚合模型

## 7.1 OperationPlan

```python
@dataclass
class OperationPlan:
    plan_id: PlanId
    plan_version: int

    instruction: str
    summary: str

    source: OperationSource
    risk_level: RiskLevel

    status: OperationPlanStatus

    requires_confirmation: bool
    confirmation_policy: ConfirmationPolicy

    operations: tuple[KnowledgeOperation, ...]
    affected_resources: tuple[AffectedResource, ...]
    warnings: tuple[OperationWarning, ...]

    failure_policy: FailurePolicy
    rollback_policy: RollbackPolicy

    idempotency_key: IdempotencyKey
    integrity_hash: Sha256Hash

    created_at: datetime
    expires_at: datetime | None

    confirmed_at: datetime | None
    confirmed_by: ActorRef | None

    execution_id: ExecutionId | None
    revision: int
```

---

## 7.2 KnowledgeOperation

```python
@dataclass
class KnowledgeOperation:
    operation_id: OperationId
    operation_type: OperationType
    order: int

    description: str
    reason: str

    risk_level: RiskLevel
    input: OperationInput

    depends_on: tuple[OperationId, ...]
    preconditions: tuple[OperationPrecondition, ...]
    postconditions: tuple[OperationPostcondition, ...]

    idempotency_key: IdempotencyKey

    timeout_seconds: int
    retry_policy: RetryPolicy
```

---

## 7.3 OperationExecution

执行记录应独立于计划保存。

```python
@dataclass
class OperationExecution:
    execution_id: ExecutionId
    plan_id: PlanId
    plan_version: int

    status: ExecutionStatus
    executor_type: ExecutorType
    executor_instance_id: str

    started_at: datetime
    completed_at: datetime | None

    operation_results: tuple[OperationResult, ...]

    rollback_available: bool
    rollback_token_hash: Sha256Hash | None

    error: DomainError | None
```

---

# 8. 状态机

## 8.1 Operation Plan 状态

```text
draft
  │
  ├── validate failed ──> rejected
  │
  ▼
pending-confirmation
  │
  ├── reject ───────────> rejected
  ├── expire ───────────> expired
  └── approve
         ▼
     confirmed
         │
         ▼
     executing
       ├───────────────┐
       ▼               ▼
   completed         failed
       │               │
       └──────┬────────┘
              ▼
         rolling-back
              │
        ┌─────┴─────┐
        ▼           ▼
   rolled-back   rollback-failed
```

---

## 8.2 状态定义

### draft

计划正在生成或编辑，尚未完成验证。

### pending-confirmation

计划已验证，但根据风险与权限策略需要用户确认。

### confirmed

用户已确认，计划内容被冻结，等待执行。

### executing

执行器已获得执行锁并开始执行。

### completed

所有要求执行的操作均成功完成。

### partially-completed

部分操作成功，部分操作失败，且未完全回滚。

### failed

计划执行失败，未产生成功结果，或失败后已按策略停止。

### rejected

计划被用户或策略拒绝。

### expired

计划超过有效期，不得继续确认或执行。

### rolling-back

正在执行回滚。

### rolled-back

所有可回滚操作均已恢复。

### rollback-failed

回滚未完全成功，需要人工处理。

### cancelled

执行开始前被用户取消，或后台任务被安全终止。

---

# 9. 计划来源

```python
class OperationSourceType(str, Enum):
    USER = "user"
    AGENT = "agent"
    RULE = "rule"
    AUTOMATION = "automation"
    REMOTE = "remote"
    PLUGIN = "plugin"
    SYSTEM = "system"
```

来源不等于权限。

例如：

- 用户发出的自然语言指令仍需经过 Agent 规划。
- Remote 来源默认没有 Critical 写权限。
- Rule 来源只能执行规则白名单中的操作。
- Automation 来源只能执行用户预授权范围内的操作。

---

# 10. 风险等级

## 10.1 Low

典型操作：

- 创建 Inbox 临时记录。
- 添加已有标签。
- 补充更新时间。
- 创建任务草稿。
- 修复确定性的格式问题。

默认行为：

- 本地可信环境中可按用户配置自动执行。
- 仍需记录日志。
- 必须具备幂等键。
- 必须校验路径和版本。

---

## 10.2 Medium

典型操作：

- 修改 YAML。
- 修改单个任务状态。
- 修改少量正文。
- 移动单个文件。
- 创建正式项目文件。

默认行为：

- 建议展示预览。
- 用户可配置是否每次确认。
- 远程来源必须确认。

---

## 10.3 High

典型操作：

- 批量移动。
- 批量重命名。
- 替换较大正文。
- 批量修改标签。
- 调用其他插件执行写操作。
- 修改项目结构。

默认行为：

- 必须明确确认。
- 必须显示影响范围。
- 必须保留回滚数据。
- 计划有效期应较短。

---

## 10.4 Critical

典型操作：

- 删除文件。
- 永久删除。
- 清空正文。
- 批量删除。
- 修改保护目录。
- 远程执行危险操作。
- 调用具有外部副作用的插件能力。

默认行为：

- 必须本地用户显式确认。
- 不允许自动化直接执行。
- 不允许远程确认代替本地确认。
- 必须使用一次性确认令牌。
- 必须保存完整快照。
- 默认只能移动到系统废纸篓，不能永久删除。

---

# 11. 风险计算

计划风险不能只取 Agent 返回值。

最终风险由规则引擎确定：

```text
最终风险 =
    操作类型基础风险
  + 影响文件数量
  + 影响内容规模
  + 来源风险
  + 目录敏感级别
  + 是否可回滚
  + 是否调用外部插件
  + 是否包含删除
```

示例规则：

```python
if operation.type == DELETE_NOTE:
    risk = CRITICAL

if affected_file_count >= 20:
    risk = max(risk, HIGH)

if source.type == REMOTE and operation.is_write:
    risk = max(risk, HIGH)

if target_path.is_protected:
    risk = CRITICAL

if rollback_policy == NONE:
    risk = max(risk, HIGH)
```

Plan 的风险等级必须大于或等于内部所有操作的最高风险。

---

# 12. 确认策略

## 12.1 ConfirmationPolicy

```python
@dataclass(frozen=True)
class ConfirmationPolicy:
    mode: ConfirmationMode
    approver_types: tuple[ApproverType, ...]
    allow_partial_approval: bool
    expires_in_seconds: int
    require_diff_preview: bool
    require_local_presence: bool
```

---

## 12.2 确认模式

```text
none
implicit
explicit
two-step
```

### none

只允许 Low 风险且已被用户预授权的操作。

### implicit

用户点击“应用修改”即视为确认。

### explicit

必须显示完整计划，并由用户明确确认。

### two-step

用于 Critical 操作：

1. 查看风险说明。
2. 再次确认并输入确认短语或执行系统确认动作。

---

## 12.3 确认令牌

确认令牌必须包含或关联：

- `planId`
- `planVersion`
- `integrityHash`
- `approverId`
- `approvedOperationIds`
- `issuedAt`
- `expiresAt`
- 随机 nonce

确认令牌必须：

- 一次性使用。
- 服务端签名。
- 过期后失效。
- 不能用于其他计划版本。
- 不能扩大原批准范围。

---

## 12.4 部分确认

只有 `allowPartialApproval = true` 时允许部分确认。

部分确认必须满足依赖闭包。

例如：

```text
operation-3 depends_on operation-2
operation-2 depends_on operation-1
```

用户批准 `operation-3` 时，必须同时批准 1、2、3。

否则返回：

```text
OPERATION_DEPENDENCY_NOT_APPROVED
```

---

# 13. 操作依赖

操作通过 `dependsOn` 建立有向无环图。

要求：

1. 所有依赖操作必须存在于同一 Plan。
2. 操作不能依赖自己。
3. 依赖图不能形成环。
4. 依赖项必须先执行。
5. 被跳过的依赖会导致下游操作跳过或失败。
6. 并行执行只允许在没有依赖关系时进行。

第一版建议默认顺序执行，后续再支持安全并行。

---

# 14. 前置条件

## 14.1 通用前置条件

```text
file-exists
file-not-exists
folder-exists
version-match
content-hash-match
metadata-hash-match
path-available
permission-granted
plugin-available
capability-supported
plan-not-expired
vault-match
executor-online
```

---

## 14.2 示例

```json
{
  "type": "version-match",
  "target": "Projects/AutoUp/设计文档.md",
  "expected": "sha256:..."
}
```

---

## 14.3 校验阶段

前置条件需要在两个阶段校验。

### 计划生成后

用于判断计划是否具备可执行性。

### 真正写入前

用于防止用户在预览和确认期间修改文件。

第二次校验失败时必须停止，不能继续使用旧计划。

---

# 15. 后置条件

后置条件用于确认写入结果符合预期。

类型包括：

```text
file-created
file-moved
file-version-changed
metadata-field-equals
task-status-equals
plugin-invocation-succeeded
content-contains
path-does-not-exist
```

例如：

```json
{
  "type": "metadata-field-equals",
  "target": "Projects/AutoUp/任务.md",
  "field": "status",
  "expected": "active"
}
```

写入成功但后置条件失败时，操作应标记为失败，并根据 `FailurePolicy` 决定是否回滚。

---

# 16. 乐观锁与版本校验

每个修改型操作必须携带一个或多个版本信息：

- `expectedVersionHash`
- `expectedContentHash`
- `expectedMetadataHash`
- `oldTextHash`

执行前实际哈希不一致时返回：

```text
NOTE_VERSION_CONFLICT
```

禁止行为：

- 自动覆盖新版本。
- 忽略冲突继续执行。
- 使用 Agent 推测合并。
- 悄悄重新生成内容后执行。

正确流程：

```text
检测冲突
→ 停止操作
→ 返回当前文件版本
→ Agent 重新读取
→ 生成新的 planVersion
→ 用户重新确认
```

---

# 17. 幂等性

## 17.1 Plan 级幂等

同一个 `plan.idempotencyKey` 重复提交时：

- 如果尚未执行，返回已有 Plan。
- 如果已经执行成功，返回原 ExecutionResult。
- 如果正在执行，返回当前执行状态。
- 如果执行失败，根据重试策略决定是否允许重新执行。

---

## 17.2 Operation 级幂等

每个操作也必须有独立 `idempotencyKey`。

例如创建任务：

```text
task:create:auto-up-wechat:2026-07-15
```

执行器在写入前应查询操作日志，防止：

- 插件重连后重复创建。
- HTTP 超时导致客户端重试。
- Agent 重复提交。
- WebSocket 消息重复送达。

---

# 18. 计划完整性

对以下字段进行规范化序列化后计算 SHA-256：

- `planId`
- `planVersion`
- `instruction`
- `summary`
- `source`
- `riskLevel`
- `operations`
- `failurePolicy`
- `rollbackPolicy`
- `expiresAt`
- `idempotencyKey`

不参与哈希：

- 当前状态。
- 确认时间。
- 执行时间。
- UI 展示数据。
- 运行时进度。

执行前必须重新计算，并与 `integrityHash` 比较。

不一致时返回：

```text
OPERATION_PLAN_INTEGRITY_FAILED
```

---

# 19. 执行锁

同一 Plan 同一时间只能由一个执行器执行。

建议执行锁结构：

```python
@dataclass
class ExecutionLease:
    plan_id: PlanId
    execution_id: ExecutionId
    owner_instance_id: str
    acquired_at: datetime
    expires_at: datetime
    heartbeat_at: datetime
```

要求：

- 获取锁必须是原子操作。
- 执行器定期续租。
- 锁超时后不能直接重新执行，必须先检查已产生的副作用。
- 成功或失败后释放锁。
- 插件和 Headless Executor 不能同时执行同一 Plan。

---

# 20. 执行流程

## 20.1 标准流程

```text
1. 加载 Operation Plan
2. 检查状态为 confirmed
3. 检查 planVersion
4. 检查计划是否过期
5. 校验确认令牌
6. 校验完整性哈希
7. 检查权限
8. 获取执行锁
9. 创建 OperationExecution
10. 再次校验全部前置条件
11. 生成或确认回滚快照
12. 按依赖顺序执行操作
13. 校验每项后置条件
14. 记录单项执行结果
15. 处理失败策略
16. 更新 Plan 状态
17. 发布领域事件
18. 更新索引与知识图谱
19. 释放执行锁
20. 返回 ExecutionResult
```

---

## 20.2 单项操作执行流程

```text
检查是否已执行
→ 检查依赖结果
→ 校验前置条件
→ 创建快照
→ 执行写入
→ 校验后置条件
→ 记录 before/after hash
→ 标记成功
```

---

# 21. 失败策略

```python
class FailurePolicy(str, Enum):
    STOP = "stop"
    CONTINUE = "continue"
    ROLLBACK_ALL = "rollback-all"
```

## 21.1 stop

当前操作失败后：

- 停止后续未执行操作。
- 已成功操作保持不变。
- Plan 标记 `partially-completed` 或 `failed`。
- 如果支持回滚，可提示用户执行回滚。

适用于：

- 普通批量整理。
- 操作间存在业务关联但不要求严格事务。

---

## 21.2 continue

当前操作失败后继续执行其他独立操作。

要求：

- 失败操作不能是后续操作的依赖。
- 适合互相独立的批量修复。
- 最终状态可能为 `partially-completed`。

适用于：

- 批量补充标签。
- 健康检查修复。
- 多篇无关联笔记的格式整理。

---

## 21.3 rollback-all

任一操作失败时，回滚已经成功的全部操作。

适用于：

- 项目目录整体迁移。
- 多文件原子性较强的重构。
- 文件移动与链接同步更新。
- 需要保证整体一致性的批量修改。

第一版需要说明：

文件系统无法提供真正数据库级事务，因此 `rollback-all` 是补偿事务，不是绝对原子事务。

---

# 22. 重试策略

重试只允许用于可安全重试且具备幂等性的错误。

```python
@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    initial_delay_ms: int
    max_delay_ms: int
    backoff_multiplier: float
    retryable_error_codes: tuple[str, ...]
```

可以重试：

- 临时文件占用。
- 网络瞬时错误。
- Agent 通讯超时。
- 插件能力临时未就绪。
- 数据库连接瞬时错误。

不得自动重试：

- 版本冲突。
- 权限不足。
- 用户拒绝。
- 路径冲突。
- Schema 校验失败。
- 确认令牌失效。
- 未知操作类型。
- Critical 操作失败。

---

# 23. 回滚设计

## 23.1 回滚策略

```python
class RollbackMode(str, Enum):
    NONE = "none"
    SNAPSHOT = "snapshot"
    INVERSE_OPERATION = "inverse-operation"
    HYBRID = "hybrid"
```

### snapshot

保存执行前文件完整内容或必要快照。

优点：

- 恢复可靠。
- 适合正文修改。

缺点：

- 占用存储。

### inverse-operation

为操作生成逆操作。

例如：

```text
move A → B
逆操作：move B → A
```

优点：

- 存储较小。

缺点：

- 对复杂文本修改不可靠。

### hybrid

根据操作类型自动选择，是推荐默认方案。

---

## 23.2 不同操作的回滚数据

### create-note

回滚：删除刚创建的文件，默认移动到废纸篓。

### update-note

回滚：恢复修改前正文和元数据快照。

### move-note

回滚：从目标路径移动回源路径，同时恢复链接。

### rename-note

回滚：恢复原文件名和链接。

### update-metadata

回滚：恢复修改前 YAML。

### delete-note

回滚：从系统废纸篓或快照恢复。

### create-task

回滚：删除对应任务块或恢复任务文件快照。

### invoke-plugin

只有适配器声明 `supportsRollback = true` 时才允许自动回滚。

---

## 23.3 回滚冲突

回滚前仍需检查当前版本。

例如：

```text
Agent 修改笔记
→ 用户随后手动继续编辑
→ 用户点击撤销 Agent 操作
```

此时不能直接覆盖用户的新内容。

可选策略：

- 拒绝自动回滚并提示冲突。
- 生成三方差异。
- 生成新的恢复计划。
- 只回滚可精确定位的 Patch。

禁止直接用旧快照覆盖当前文件。

---

# 24. 操作执行器接口

```python
class OperationExecutor(Protocol):
    executor_type: ExecutorType

    async def supports(
        self,
        operation: KnowledgeOperation,
        context: ExecutionContext,
    ) -> bool:
        ...

    async def prepare(
        self,
        operation: KnowledgeOperation,
        context: ExecutionContext,
    ) -> PreparedOperation:
        ...

    async def execute(
        self,
        prepared: PreparedOperation,
        context: ExecutionContext,
    ) -> OperationResult:
        ...

    async def rollback(
        self,
        rollback_data: RollbackData,
        context: ExecutionContext,
    ) -> OperationResult:
        ...
```

---

# 25. 执行器类型

## 25.1 Obsidian Executor

通过 Obsidian Vault API 执行。

负责：

- 文件创建与修改。
- 重命名和移动。
- 更新双向链接。
- 感知当前编辑器。
- 调用其他插件。
- 展示实时 Diff。
- 与 Obsidian 撤销栈配合。

优先级最高。

---

## 25.2 Headless Executor

直接操作本地 Vault 文件。

第一阶段只开放低风险能力：

- 查询。
- 创建 Inbox 笔记。
- 创建待确认任务。
- 追加低风险记录。

默认禁止：

- 删除。
- 批量移动。
- 批量重命名。
- 调用 Obsidian 插件。
- 修改当前正在编辑的文件。
- 修改保护目录。

---

## 25.3 Plugin Capability Executor

只允许调用注册的插件能力：

```python
@dataclass(frozen=True)
class PluginCapability:
    plugin_id: str
    capability_id: str
    input_schema: dict
    output_schema: dict
    risk_level: RiskLevel
    supports_rollback: bool
    timeout_seconds: int
```

Agent 不得传入任意 JavaScript。

---

# 26. Operation Handler Registry

```python
class OperationHandlerRegistry:
    def register(
        self,
        operation_type: OperationType,
        handler: OperationHandler,
    ) -> None:
        ...

    def resolve(
        self,
        operation_type: OperationType,
    ) -> OperationHandler:
        ...
```

每种 Operation 都有独立 Handler：

```text
CreateNoteHandler
UpdateNoteHandler
MoveNoteHandler
RenameNoteHandler
DeleteNoteHandler
UpdateMetadataHandler
CreateTaskHandler
UpdateTaskHandler
InvokePluginHandler
```

禁止在一个巨大 `OperationExecutor` 中使用数百行 `if/elif`。

---

# 27. Operation Plan Builder

Builder 负责将领域意图转换成合法计划。

```python
class OperationPlanBuilder:
    def add_operation(...)
    def add_warning(...)
    def set_failure_policy(...)
    def set_rollback_policy(...)
    def calculate_risk(...)
    def validate_dependencies(...)
    def build() -> OperationPlan
```

Builder 必须：

- 自动分配顺序。
- 检查依赖图。
- 汇总影响资源。
- 计算风险。
- 决定是否需要确认。
- 生成幂等键。
- 计算完整性哈希。
- 填充计划有效期。

Builder 不负责执行。

---

# 28. Validator 分层

## 28.1 Contract Validator

校验 JSON Schema 和字段类型。

## 28.2 Domain Validator

校验：

- 状态转换。
- 操作类型与输入模型匹配。
- 风险等级。
- 依赖关系。
- 确认策略。
- 计划有效期。

## 28.3 Policy Validator

校验：

- 用户权限。
- 来源权限。
- 目录保护策略。
- 远程写入策略。
- 自动化授权范围。

## 28.4 Runtime Validator

执行前校验：

- 当前文件版本。
- 路径冲突。
- 插件在线状态。
- 执行器可用性。
- 计划锁。
- 幂等记录。

---

# 29. 权限模型

权限建议采用细粒度命名：

```text
note.create
note.update
note.move
note.rename
note.delete

task.create
task.update
task.delete

project.update
project.archive

plugin.invoke

operation.confirm
operation.execute
operation.rollback
operation.execute.high-risk
operation.execute.critical
```

执行权限计算：

```text
实际权限 =
    身份权限
  ∩ 来源权限
  ∩ 当前设备权限
  ∩ Vault 权限
  ∩ 用户策略
  ∩ 操作风险允许范围
```

---

# 30. 保护目录

用户可配置保护目录：

```yaml
protected_paths:
  - path: "Archive/重要资料"
    minimum_risk: critical
    allow_remote_write: false
    allow_automation_write: false

  - path: ".obsidian"
    deny_all_agent_writes: true
```

默认保护：

- `.obsidian/`
- `.git/`
- `.trash/`
- Agent 私密配置目录
- 用户明确锁定的项目目录

---

# 31. 远程指令规则

外部通讯软件请求必须先进入统一 Agent 流程。

默认远程权限：

```text
knowledge.search
note.read
task.read
project.read
note.create.inbox
task.create.pending
```

默认不允许：

- 直接删除。
- 直接批量改写。
- 直接移动项目。
- 直接调用其他插件。
- 远程批准 Critical 操作。

远程高风险操作流程：

```text
远程发送指令
→ Agent 生成 Plan
→ 返回摘要
→ Obsidian 插件本地弹出确认
→ 本地用户确认
→ 插件执行
→ 远程返回结果
```

---

# 32. 审计日志

所有 Plan 和 Execution 必须产生审计日志。

```python
@dataclass
class AuditRecord:
    audit_id: str
    event_type: str

    plan_id: PlanId
    plan_version: int
    execution_id: ExecutionId | None
    operation_id: OperationId | None

    actor: ActorRef
    source: OperationSource

    action: str
    result: str

    affected_paths: tuple[str, ...]
    before_hashes: dict[str, str]
    after_hashes: dict[str, str]

    timestamp: datetime
    trace_id: str

    details: dict[str, Any]
```

审计日志不得记录：

- API 密钥。
- 完整模型认证信息。
- 通讯软件令牌。
- 不必要的敏感正文。

正文快照和审计元数据应分开存储。

---

# 33. 领域事件

建议发布：

```text
operation-plan.created
operation-plan.validated
operation-plan.confirmation-required
operation-plan.confirmed
operation-plan.rejected
operation-plan.expired

operation-execution.started
operation-execution.progress
operation-execution.completed
operation-execution.partially-completed
operation-execution.failed

operation.started
operation.completed
operation.failed
operation.skipped

operation-rollback.started
operation-rollback.completed
operation-rollback.failed
```

事件消费者：

- Obsidian UI。
- 索引更新器。
- 知识图谱更新器。
- 审计模块。
- 通讯网关。
- 自动化调度器。
- 操作历史视图。

---

# 34. 数据持久化

## 34.1 operation_plans

建议字段：

```text
plan_id
plan_version
status
instruction
summary
source_type
source_data_json
risk_level
requires_confirmation
confirmation_policy_json
operations_json
affected_resources_json
warnings_json
failure_policy
rollback_policy_json
idempotency_key
integrity_hash
created_at
expires_at
confirmed_at
confirmed_by
execution_id
revision
```

唯一约束：

```text
(plan_id, plan_version)
idempotency_key
```

---

## 34.2 operation_executions

```text
execution_id
plan_id
plan_version
status
executor_type
executor_instance_id
started_at
completed_at
rollback_available
rollback_token_hash
error_json
```

---

## 34.3 operation_results

```text
execution_id
operation_id
status
attempt_count
started_at
completed_at
before_version_hash
after_version_hash
affected_paths_json
rollback_data_ref
error_json
```

---

## 34.4 operation_snapshots

```text
snapshot_id
execution_id
operation_id
resource_path
snapshot_type
content_location
content_hash
created_at
expires_at
encrypted
```

大体积快照不应直接塞进数据库行，可以保存到本地快照目录并在数据库中存引用。

---

# 35. 快照存储

建议目录：

```text
.obsidian-agent-data/
└── operation-snapshots/
    └── {execution_id}/
        ├── manifest.json
        ├── op_001_before.md
        ├── op_001_metadata.json
        └── op_002_before.md
```

要求：

- 默认不与知识库正文混放。
- 可以配置保留时间。
- 支持加密。
- 清理前确保没有可用回滚引用。
- 快照目录不进入知识索引。

---

# 36. API 设计

## 36.1 创建或获取计划

```text
POST /api/v1/operations/plans
```

## 36.2 获取计划

```text
GET /api/v1/operations/plans/{planId}
```

## 36.3 获取预览

```text
GET /api/v1/operations/plans/{planId}/preview
```

## 36.4 确认计划

```text
POST /api/v1/operations/plans/{planId}/confirm
```

## 36.5 拒绝计划

```text
POST /api/v1/operations/plans/{planId}/reject
```

## 36.6 执行计划

```text
POST /api/v1/operations/plans/{planId}/execute
```

## 36.7 查询执行状态

```text
GET /api/v1/operations/executions/{executionId}
```

## 36.8 取消执行

```text
POST /api/v1/operations/executions/{executionId}/cancel
```

只允许安全取消尚未开始的操作，不能强行终止正在进行的原子文件写入。

## 36.9 回滚

```text
POST /api/v1/operations/executions/{executionId}/rollback
```

---

# 37. Preview 模型

预览不只是显示摘要，还要提供结构化 Diff。

```python
@dataclass
class OperationPlanPreview:
    plan_id: PlanId
    plan_version: int
    summary: str
    risk_level: RiskLevel

    affected_file_count: int
    created_files: tuple[str, ...]
    updated_files: tuple[str, ...]
    moved_files: tuple[MovePreview, ...]
    deleted_files: tuple[str, ...]

    diffs: tuple[ResourceDiff, ...]
    warnings: tuple[OperationWarning, ...]

    rollback_available: bool
```

正文 Diff 至少包含：

```text
path
change_type
before_excerpt
after_excerpt
line_changes
metadata_changes
```

---

# 38. 执行结果模型

```python
@dataclass
class OperationResult:
    operation_id: OperationId
    status: OperationResultStatus

    attempt_count: int

    before_version_hash: Sha256Hash | None
    after_version_hash: Sha256Hash | None

    affected_paths: tuple[VaultPath, ...]

    rollback_available: bool
    rollback_data_ref: str | None

    error: OperationError | None
```

计划结果：

```python
@dataclass
class OperationExecutionResult:
    execution_id: ExecutionId
    plan_id: PlanId
    plan_version: int

    status: ExecutionStatus
    operation_results: tuple[OperationResult, ...]

    started_at: datetime
    completed_at: datetime | None

    rollback_available: bool
    rollback_token: str | None
```

---

# 39. 错误码

## 39.1 计划错误

```text
OPERATION_PLAN_NOT_FOUND
OPERATION_PLAN_VERSION_MISMATCH
OPERATION_PLAN_INVALID_STATE
OPERATION_PLAN_EXPIRED
OPERATION_PLAN_INTEGRITY_FAILED
OPERATION_PLAN_ALREADY_EXECUTED
OPERATION_PLAN_VALIDATION_FAILED
```

## 39.2 确认错误

```text
OPERATION_CONFIRMATION_REQUIRED
OPERATION_CONFIRMATION_INVALID
OPERATION_CONFIRMATION_EXPIRED
OPERATION_CONFIRMATION_SCOPE_INVALID
OPERATION_DEPENDENCY_NOT_APPROVED
```

## 39.3 执行错误

```text
OPERATION_EXECUTION_LOCKED
OPERATION_EXECUTOR_UNAVAILABLE
OPERATION_TYPE_UNSUPPORTED
OPERATION_PRECONDITION_FAILED
OPERATION_POSTCONDITION_FAILED
OPERATION_TIMEOUT
OPERATION_RETRY_EXHAUSTED
```

## 39.4 文件错误

```text
NOTE_NOT_FOUND
NOTE_VERSION_CONFLICT
NOTE_PATH_CONFLICT
NOTE_PATH_PROTECTED
NOTE_WRITE_FAILED
NOTE_MOVE_FAILED
```

## 39.5 插件错误

```text
PLUGIN_NOT_AVAILABLE
PLUGIN_VERSION_UNSUPPORTED
PLUGIN_CAPABILITY_NOT_SUPPORTED
PLUGIN_INVOCATION_FAILED
PLUGIN_ROLLBACK_NOT_SUPPORTED
```

## 39.6 回滚错误

```text
ROLLBACK_NOT_AVAILABLE
ROLLBACK_TOKEN_INVALID
ROLLBACK_VERSION_CONFLICT
ROLLBACK_PARTIALLY_FAILED
ROLLBACK_FAILED
```

---

# 40. 与 contracts 的映射

`packages/contracts` 负责传输结构：

```text
OperationPlanContract
KnowledgeOperationContract
ConfirmOperationPlanRequest
ExecuteOperationPlanRequest
OperationExecutionResultContract
RollbackRequest
```

`packages/domain` 负责领域约束：

```text
OperationPlan
KnowledgeOperation
ConfirmationPolicy
RollbackPolicy
OperationExecution
```

`packages/application` 负责映射：

```text
Contract DTO
    ↓ mapper
Domain Model
    ↓ use case
Executor Port
```

禁止 Domain 直接依赖 JSON Schema 或 FastAPI。

---

# 41. 与 Application 层的关系

建议用例：

```text
BuildOperationPlanUseCase
ValidateOperationPlanUseCase
PreviewOperationPlanUseCase
ConfirmOperationPlanUseCase
RejectOperationPlanUseCase
ExecuteOperationPlanUseCase
CancelOperationExecutionUseCase
RollbackOperationExecutionUseCase
GetOperationHistoryUseCase
```

每个 Use Case 单独实现，不建立一个过大的 `OperationService`。

---

# 42. 与 Rule Engine 的关系

规则引擎负责：

- 计算风险等级。
- 判断是否需要确认。
- 判断操作来源是否允许。
- 检查保护目录。
- 决定计划有效期。
- 决定默认失败策略。
- 决定默认回滚策略。

规则引擎不能直接执行写操作。

---

# 43. 与知识索引的关系

执行成功后发布事件：

```text
note.created
note.updated
note.moved
note.deleted
```

Knowledge Engine 监听事件后进行增量更新。

要求：

- 文件写入成功是主流程。
- 索引更新失败不能回滚已经成功的知识库写入。
- 索引失败应创建补偿 Job。
- 向量索引和图谱都必须可以从 Markdown 重建。

---

# 44. 与 Obsidian 插件的关系

插件负责：

- 展示 Operation Plan。
- 显示 Diff。
- 收集用户确认。
- 获取本地确认令牌。
- 通过 Vault API 执行。
- 返回执行结果。
- 展示失败原因。
- 提供撤销入口。

插件不能：

- 自己改变 Agent 生成的计划内容。
- 忽略版本校验。
- 将未确认计划直接执行。
- 将未知 Operation 当作成功。
- 伪造执行结果。

---

# 45. 典型案例

## 45.1 优化当前笔记

用户：

```text
帮我优化当前笔记并按照规范补充标签。
```

计划：

```text
1. update-note：替换指定段落
2. update-metadata：添加已有标签
```

风险：

```text
medium
```

执行前：

- 校验当前笔记版本。
- 展示正文 Diff。
- 展示 YAML Diff。
- 用户确认。

---

## 45.2 批量整理 AutoUp 临时笔记

计划：

```text
1. 为 8 篇笔记补充 project: AutoUp
2. 将 8 篇笔记移动到 Projects/AutoUp/Inbox
3. 调用 Dataview 刷新
```

风险：

```text
high
```

策略：

```text
failurePolicy: rollback-all
rollbackMode: hybrid
```

---

## 45.3 远程创建任务

远程消息：

```text
周五前完成 AutoUp 微信公众号适配，优先级高。
```

计划：

```text
create-task
```

风险：

```text
low 或 medium
```

远程允许：

- 创建待处理任务。
- 返回创建结果。

不允许远程自动删除或移动项目文件。

---

## 45.4 删除笔记

计划：

```text
delete-note
deleteMode: trash
```

风险：

```text
critical
```

要求：

- 本地显式二次确认。
- 显示文件路径和引用关系。
- 保存完整快照。
- 禁止远程确认。
- 默认进入废纸篓。

---

# 46. 测试策略

## 46.1 单元测试

需要覆盖：

- 状态转换。
- 风险等级计算。
- 依赖图检测。
- 完整性哈希。
- 计划过期。
- 确认范围。
- 幂等键。
- 部分批准依赖闭包。
- Critical 确认策略。
- 回滚策略生成。

---

## 46.2 契约测试

验证：

- Contracts JSON 可以映射为 Domain。
- Domain 可以序列化回 Contracts。
- 新增可选字段不会破坏旧客户端。
- 未知操作类型会被拒绝。
- 枚举值与 Schema 保持一致。

---

## 46.3 集成测试

需要覆盖：

- SQLite Plan Repository。
- 执行锁。
- 文件快照。
- Obsidian Vault Executor。
- Headless Executor。
- 插件调用适配器。
- 操作日志。
- 回滚。

---

## 46.4 端到端测试

关键场景：

1. 生成计划后用户修改文件。
2. 重复点击执行。
3. HTTP 超时后客户端重试。
4. 执行一半插件崩溃。
5. 批量移动中目标路径冲突。
6. 回滚前用户再次修改文件。
7. 远程指令尝试删除文件。
8. 插件能力执行到一半失败。
9. Plan 过期后尝试确认。
10. 两个执行器同时抢占计划。
11. 依赖操作失败后下游操作处理。
12. 索引更新失败但文件写入成功。

---

# 47. 性能与规模

第一版目标：

- 单个 Plan 最多 100 个操作。
- 单个 Plan 最大影响 100 个文件。
- 单次正文替换最大 5 MB。
- Plan 预览生成时间不超过 2 秒，不包含大模型生成。
- 本地执行状态更新延迟低于 500 ms。
- 审计记录与执行结果必须实时落盘。

超过限制时，应拆分为多个 Plan 或创建后台 Job。

---

# 48. 可观测性

指标建议：

```text
operation_plan_created_total
operation_plan_confirmed_total
operation_plan_rejected_total
operation_execution_total
operation_execution_failed_total
operation_version_conflict_total
operation_rollback_total
operation_rollback_failed_total
operation_duration_seconds
operation_queue_size
```

日志必须包含：

- `traceId`
- `requestId`
- `planId`
- `executionId`
- `operationId`

---

# 49. 安全要求

1. 确认令牌不得明文长期存储。
2. Rollback Token 应只保存哈希。
3. API 密钥不得进入 Operation Plan。
4. 插件调用参数必须通过 Schema 校验。
5. 路径必须标准化并禁止路径穿越。
6. 所有写入限制在当前 Vault 内。
7. 外部来源必须经过身份验证。
8. Critical 操作必须本地确认。
9. 快照中可能包含敏感正文，应支持加密和自动清理。
10. 审计日志不能被普通 Agent 操作删除。

---

# 50. 第一阶段实现范围

第一阶段实现：

```text
OperationPlan 聚合
KnowledgeOperation 基类
CreateNoteOperation
UpdateNoteOperation
MoveNoteOperation
UpdateMetadataOperation
CreateTaskOperation
InvokePluginOperation

OperationPlanBuilder
OperationPlanValidator
RiskPolicy
ConfirmationPolicy
ExecutionLock
OperationExecutor
OperationHandlerRegistry
SnapshotStore
AuditRepository
RollbackManager
```

第一阶段暂不实现：

- 跨设备分布式锁。
- 多用户审批。
- 真正并行执行。
- 永久删除。
- 复杂三方合并。
- 插件能力自动发现市场。
- 云端多租户权限体系。

---

# 51. 推荐目录结构

```text
packages/application/
└── operations/
    ├── use_cases/
    │   ├── build_operation_plan.py
    │   ├── preview_operation_plan.py
    │   ├── confirm_operation_plan.py
    │   ├── execute_operation_plan.py
    │   ├── rollback_operation.py
    │   └── get_operation_history.py
    │
    ├── services/
    │   ├── operation_plan_builder.py
    │   ├── operation_plan_validator.py
    │   ├── risk_calculator.py
    │   ├── confirmation_service.py
    │   ├── execution_coordinator.py
    │   └── rollback_manager.py
    │
    ├── ports/
    │   ├── operation_executor.py
    │   ├── snapshot_store.py
    │   ├── execution_lock.py
    │   ├── audit_repository.py
    │   └── confirmation_token_service.py
    │
    └── mappers/
        ├── operation_contract_mapper.py
        └── execution_result_mapper.py
```

执行适配器：

```text
adapters/
└── operations/
    ├── obsidian/
    │   ├── executor.py
    │   └── handlers/
    ├── headless/
    │   ├── executor.py
    │   └── handlers/
    ├── snapshots/
    ├── locks/
    └── audit/
```

---

# 52. 开发顺序

```text
1. 固定 OperationPlan 状态机
2. 固定操作基类和错误码
3. 实现 Builder
4. 实现 Domain Validator
5. 实现 Risk Policy
6. 实现 Confirmation Token
7. 实现 Execution Lock
8. 实现 Handler Registry
9. 实现 Create/Update/Move 三种基础 Handler
10. 实现 Snapshot Store
11. 实现 Rollback Manager
12. 实现 Audit Repository
13. 接入 Obsidian 插件预览
14. 增加契约测试
15. 增加端到端冲突测试
```

---

# 53. 架构决策结论

Operation Plan 最终采用：

```text
不可变计划
+ 结构化操作
+ 风险分级
+ 显式确认
+ 完整性哈希
+ 乐观锁
+ 幂等键
+ 执行租约
+ 前置与后置条件
+ 补偿事务
+ 快照与逆操作回滚
+ 全链路审计
```

系统中所有写入口必须收敛到：

```text
Build Plan
→ Validate
→ Preview
→ Confirm
→ Execute
→ Verify
→ Audit
→ Rollback
```

任何模块都不得绕过这条链路直接修改知识库。

这套协议是整个知识库 Agent 的安全基础。后续无论增加自然语言指令、自动归档、知识图谱、远程通讯软件、定时任务还是其他插件调用，都只需要生成新的 Operation Plan 或扩展新的 Operation Handler，而不需要重新设计写入安全机制。
