# obsidian-knowledge-agent-application

Operation Plan 的应用层实现，负责：

- 计划构建与风险计算
- 计划验证与确认令牌
- 执行编排、幂等、执行锁
- 快照、审计与补偿回滚
- Preview 与 Use Case

该包只依赖 `obsidian-knowledge-agent-domain` 中的纯领域模型，具体文件系统、SQLite、Obsidian 和插件调用由 adapters 实现。
