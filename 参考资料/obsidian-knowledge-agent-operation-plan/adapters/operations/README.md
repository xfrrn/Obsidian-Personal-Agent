# operation adapters

提供 Operation Plan 的参考基础设施实现：

- 本地测试 Vault 文件执行器
- 六类 Operation Handler
- HMAC 一次性确认令牌
- 内存 Plan、Execution、Snapshot、Audit、Idempotency Repository
- 内存执行租约
- 静态细粒度权限校验
- 可注册的插件能力调用器

`LocalFilesystemVault` 用于 Headless 和端到端测试。正式 Obsidian 插件应在 TypeScript 端实现同样的 Handler 契约，并优先通过 Obsidian Vault API 执行。
