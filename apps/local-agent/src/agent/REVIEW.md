# CodeX-Agent 架构审查报告

对照 codex-rs 的参考实现，对当前 agent 项目的模块完整性、实现质量和关键缺陷进行的系统审查。

---

## 一、总体评价

项目整体架构质量很高。以下核心设计决策是正确的：

- **组合根模式**：`core/loop.py:create_session()` 作为唯一依赖组装点，各模块只接收已创建好的依赖
- **协议层隔离**：`protocol/op.py` 不依赖任何项目内模块，CLI / Web / 测试可各自引用
- **事件总线**：`TurnEventBus` + `Mailbox` 模式使内部状态变更可被多个消费者观察而不耦合
- **权限流水线**：`PermissionRequirement → Policy.decide() → Manager.authorize()` 三层分离，职责清晰
- **Turn 不可变快照**：`TurnContext` 是 frozen dataclass，确保回合运行中不会被后续输入修改配置

但以下方面存在需要修复的缺陷或缺失的必要功能。

---

## 二、现有模块的具体缺陷

### 2.1 ProcessManager 输出缓冲区：只保留尾部（中危）

**文件**：`tools/processes.py:71-78, 291-304`

当前实现：

```python
def push_output(self, chunk: bytes) -> None:
    # ponytail: 首版只保留尾部；确认启动诊断经常丢失时再换 HeadTailBuffer。
    self.omitted_bytes = _append_bounded(self.output, chunk, self.omitted_bytes)
```

`_append_bounded()` 在超限时从头部丢弃旧数据（`del buffer[:dropped]`）。这意味着：
- 当 `npm install` 输出 5000 行时，模型只能看到最后 ~200 行
- **编译错误、堆栈跟踪、测试失败的第一条错误（根因）通常在输出的开头**
- codex-rs 使用 `HeadTailBuffer`：保留头部 + 尾部，中间插入 `"... N bytes omitted ..."` 标记

**建议**：实现 HeadTailBuffer，保留前 50% 和后 50% 的输出。这直接影响模型诊断问题的能力。

### 2.2 缺少命令安全过滤（ExecPolicy）（高危）

**论据**：codex-rs 在 `execpolicy/src/policy.rs` 中实现了完整的三态命令策略：

```
Decision::Allow   — 直接放行（如 ls, cat, echo, git status）
Decision::Prompt  — 需要用户确认
Decision::Forbidden — 直接拒绝（如 rm -rf, sudo, chmod 777）
```

当前项目中 `exec_command` 的处理流程是：

```
模型调用 exec_command → 权限检查（沙盒/宿主） → 直接执行
```

**没有任何环节检查"这条命令本身是否安全"**。即使运行在只读沙盒中，以下命令仍然危险：
- `curl ... | sh`（可能绕过沙盒网络限制？取决于沙盒网络配置）
- 读取敏感文件并回传（如果没配 BLOCKED 网络）
- 消耗大量资源的 fork bomb

**建议**：至少实现一个基于前缀的命令白名单/黑名单，哪怕只支持 `["ls", "cat", "echo", "grep", "find", "git", "npm", "python", ...]` 等基础命令。

### 2.3 SessionStore.list() 无分页（低危）

**文件**：`storage.py:76-85`

```python
def list(self) -> tuple[StoredSession, ...]:
    # 一次性加载全部会话，没有 LIMIT/OFFSET
```

当会话数量超过几百个时，这个查询会让内存和响应时间出现问题。codex-rs 的 `list_threads` 返回 `ThreadPage`，包含 cursor 分页。

**建议**：添加 `limit` + `offset` 参数，或基于 `updated_at` 的 cursor 分页。

### 2.4 Compaction 触发条件单一（低危）

**文件**：`core/agent_loop.py:333-353`

当前只在两个条件触发压缩：
1. 手动：模型调用 `new_context_window` 后
2. 自动：活跃 token 数超过 `auto_compact_threshold`（90% 窗口）

codex-rs 额外支持：
- **ContextLimit** 触发：模型返回 context_length_exceeded 错误时
- **ModelDownshift** 触发：模型降级到更小上下文窗口时
- **CompHashChanged** 触发：编译哈希变化需要重新压缩时

**建议**：至少增加对 context_length_exceeded 错误的捕获和自动重试压缩。

### 2.5 get_context_remaining 缺少双模式（低危）

**文件**：`tools/handlers/get_context_remaining.py:36-55`

当前只返回两种剩余量。codex-rs 支持两种模式：
- `Total`：按全部上下文窗口计算剩余
- `BodyAfterPrefix`：减去系统提示词占用后计算剩余（更准确反映模型实际可用空间）

**建议**：增加对系统提示词 token 数的估算，在 BodyAfterPrefix 模式下返回更保守的数字。

### 2.6 会话恢复时不处理 running 状态的残留工具调用（中危）

**文件**：`core/loop.py:155-164`

```python
if stored_session is not None:
    session.conversation.complete_interrupted_tools()
    # 只补齐 interrupted 结果，没有处理进程残留
```

如果上次会话在 `exec_command` 返回 `process_id` 后崩溃：
- 进程可能仍在后台运行（孤儿进程）
- 恢复后的会话无法通过 `write_stdin` 续接这些进程
- `ProcessManager` 被重新创建，所有旧进程丢失

**建议**：`ProcessManager` 中的进程生命周期不应超过单个 Agent 进程——这本身是正确的设计。但应在恢复时记录 warning 日志，告知之前的后台进程可能已成为孤儿进程。

---

## 三、必要但缺失的模块

### 3.1 view_image 工具（必须）

codex-rs 的 `view_image` 是标准工具之一。

**功能**：
1. 读取工作目录中的图片文件（PNG / JPEG / GIF / WebP）
2. 解码 + 校验 + 可选缩放（最大维度 2048px）
3. 对超过限制的图片用 `FilterType::Triangle` 高质量缩放
4. 保留 ICC profile + EXIF 元数据（包括方向）
5. 编码为 JPEG(85%质量) / PNG / WebP
6. base64 封装为 `data:image/...;base64,...` Data URL
7. 检查模型是否支持 `InputModality::Image`
8. LRU 缓存（32 个条目，64MB 总字节限制）

**复刻指南**：
```
1. 创建 tools/handlers/view_image.py
2. 在 ToolSpec 中声明参数：
   - path: string (图片相对工作目录的路径)
   - detail: "original" | "high" (默认 high = 缩放至 2048px)
3. 实现流程：
   a. 通过沙盒文件系统读取原始字节
   b. 用 PIL/Pillow 解码图片
   c. 如果是 high 模式且任一边 > 2048，用 Image.LANCZOS 缩放
   d. 编码为 JPEG (quality=85) 或 PNG
   e. base64 编码，生成 "data:image/jpeg;base64,..." 格式
   f. 返回包含 data URL 的 ViewImageOutput
4. 注册到 create_session() 中（不需要条件判断，所有现代模型都支持图片输入）
5. 权限声明：ToolAccess.READ_ONLY
6. 声明 supports_parallel_tool_calls = True（无副作用）
```

**关键细节**：
- 设置 `MAX_PROMPT_IMAGE_INPUT_BYTES = 1GB` 防止恶意输入
- JPEG 质量设为 85（平衡大小和质量）
- 保留 EXIF 方向信息（用 `PIL.ImageOps.exif_transpose`）
- 代码中注释：`# 只保留 RGB ICC profile，CMYK/YCCK 在 JPEG 解码时已转为 RGB，保留源 profile 会导致颜色错误`

### 3.2 本地文件读取工具（强烈建议）

当前项目只有 `apply_patch`（写）和 `exec_command`（shell），没有直接的文件读取工具。模型要想读文件必须用 `cat` / `type` 等 shell 命令。

codex-rs 虽然没有独立命名的 `read_file` 工具，但它的文件系统沙盒允许模型直接读取文件内容，且 `view_image` 专门处理图片。对于纯文本文件，shell `cat` 是主要的读取方式——在这个意义上当前项目没有缺失。

**但需要确保**：
- `apply_patch` 的 `description` 明确写了"本工具只修改文件，读取文件请使用只读命令"
- shell 在只读沙盒中运行 `cat` 是安全的

**结论**：如果沙盒和 exec policy 完善，可以不单独添加 read_file 工具。但建议在 `exec_command` 的 description 中明确列出推荐的文件读取命令。

---

## 四、非必须但有价值的增强

以下是在 codex-rs 中存在但对 MVP 非必须的功能。按优先级排序：

| 优先级 | 功能 | 说明 |
|--------|------|------|
| 高 | `web_search` / `web_fetch` 工具 | 允许模型获取最新的在线文档、API 参考 |
| 中 | `request_user_input` 工具（Plan Mode 专用） | 在 Plan Mode 中让模型通过工具结构化地提问 |
| 中 | `task` 工具（子 agent 调度） | 分解复杂任务给子 agent 并行处理 |
| 低 | JSONL rollout 持久化层 | codex-rs 的双存储架构（JSONL 源 + SQLite 投影），当前纯 SQLite 已能满足需求 |
| 低 | ExecPolicy 前缀匹配引擎 | 完整的 Allow/Prompt/Forbidden 三态决策 + merge_overlay |
| 低 | ApprovalPolicy 的 Granular 模式 | 5 个细分权限开关（shell/network/file_write/file_read/git） |
| 低 | Memento / Purge 双压缩策略 | 当前只有简单摘要压缩 |
| 低 | `<proposed_plan>` 流解析 | Plan Mode 中分离讨论文本和计划块进行独立渲染 |

---

## 五、模块结构建议

当前 `tools/handlers/` 每个工具一个文件，结构清晰。但以下模块可以考虑重组：

```
tools/
  handlers/
    apply_patch.py         ✅ 完善
    current_time.py        ✅ 完善
    exec_command.py        ✅ 完善
    get_context_remaining.py ✅ 需增加双模式
    new_context_window.py  ✅ 完善
    update_plan.py         ✅ 完善
    view_image.py          ❌ 缺失 — 必须添加
    write_stdin.py         ✅ 完善
```

`core/scheduling/` 目前只有 `dispatcher.py` 和 `input_queue.py`，职责较薄。可以考虑将 `agent_loop.py` 中的部分逻辑下沉到这里。

---

## 六、修复优先级汇总

### 必须修复（影响正确性和安全性）

1. **添加 view_image 工具**（见 3.1）
2. **添加命令安全过滤**（见 2.2）— 至少实现基础前缀白名单

### 强烈建议（影响模型输出质量）

3. **ProcessManager 改用 HeadTailBuffer**（见 2.1）— 直接决定模型能否看到编译错误的第一行

### 应该修复（健壮性）

4. **SessionStore.list() 加分页**（见 2.3）
5. **Compaction 增加 context_length_exceeded 触发**（见 2.4）
6. **会话恢复时记录孤儿进程 warning**（见 2.6）

### 可选优化

7. **get_context_remaining 增加 BodyAfterPrefix 模式**（见 2.5）
8. **web_search / web_fetch 工具**
9. **request_user_input 工具**（Plan Mode）
