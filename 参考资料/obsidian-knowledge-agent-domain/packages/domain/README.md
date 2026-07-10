# Obsidian Knowledge Agent Domain

这是个人知识库 Agent 的纯领域模型包。它只使用 Python 标准库，不依赖 FastAPI、Pydantic、数据库、Obsidian API 或具体模型供应商。

核心模型：

- `Note`：笔记聚合根
- `Task`：任务聚合根
- `Project`：项目聚合根
- `Tag`：受控标签聚合根
- `OperationPlan`：可审查、可确认、可回滚的写操作计划

## 安装与测试

```bash
cd packages/domain
python -m pip install -e .
python -m unittest discover -s tests -v
```

## 设计原则

1. Markdown 是知识主数据，数据库只是索引与运行状态。
2. 领域层不依赖传输层；与 `packages/contracts` 的映射由应用层完成。
3. 聚合根通过方法改变状态，避免业务代码直接修改字段。
4. 每次有效变更都会提升 `revision` 并产生领域事件。
5. 所有写入动作统一进入 `OperationPlan`，执行前校验并发、风险和确认状态。

完整设计见：`../../docs/domain-model-design.md`。
