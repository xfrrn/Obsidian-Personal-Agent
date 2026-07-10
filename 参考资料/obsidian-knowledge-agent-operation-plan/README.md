# Obsidian Knowledge Agent — Operation Plan Implementation

这是 Operation Plan 安全执行协议的第一版可运行实现。

## 目录

```text
obsidian-knowledge-agent-operation-plan/
├── packages/
│   ├── domain/
│   └── application/
├── adapters/
│   └── operations/
├── docs/
│   ├── operation-plan-safe-execution-design.md
│   └── operation-plan-implementation-guide.md
├── examples/
└── scripts/
```

## 已实现

- 风险自动计算和保护目录升级
- Plan 构建、预览、确认和执行
- HMAC 一次性确认令牌
- Plan 和 Operation 幂等
- 乐观锁和完整性哈希
- 内存锁与文件锁
- 文件快照和补偿回滚
- JSON Plan/Execution 持久化
- JSONL 审计日志
- 六种 Operation Handler
- 本地测试 Vault 原子文件写入
- 插件能力白名单调用
- 运行时版本绑定

## 验证结果

当前共 21 项自动化测试通过：Domain 10 项，Operation Plan/Application 11 项。

## 运行测试

Linux/macOS：

```bash
bash scripts/run-tests.sh
```

Windows PowerShell：

```powershell
./scripts/run-tests.ps1
```

## 安装到 Monorepo 开发环境

```bash
pip install -e packages/domain
pip install -e packages/application
pip install -e adapters/operations
```

## 运行示例

```bash
python examples/basic_execution.py
```

示例会在临时 Vault 中创建一篇 Inbox 笔记，通过完整的 Build → Execute 链路落盘。
