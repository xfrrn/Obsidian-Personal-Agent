# Local Agent

本机 Python 服务托管唯一的 CodeX-Agent 运行时和 Vault OperationPlan 执行边界。

## 运行

```powershell
python -m pip install -e apps/local-agent
$env:OBSIDIAN_AGENT_PORT="8765"
python apps/local-agent/src/main.py
```

正常使用时，插件通过 `/handshake` 发送 Vault 路径、模型地址、模型名称和 SecretStorage 中的密钥。密钥只保留在进程内存。手动调试也可以设置：

```powershell
$env:OBSIDIAN_AGENT_VAULT_ROOT="D:\Vault"
$env:OBSIDIAN_AGENT_LLM_BASE_URL="https://api.example.com/v1"
$env:OBSIDIAN_AGENT_LLM_MODEL="model-name"
$env:OBSIDIAN_AGENT_LLM_API_KEY="..."
```

## 接口

```text
GET  /health
POST /handshake
GET  /identity
GET  /tools
POST /policy
GET/POST /api/sessions
GET  /api/sessions/{id}
POST /api/sessions/{id}/archive
POST /api/sessions/{id}/messages/stream
POST /api/sessions/{id}/approvals
POST /operations
GET  /operations/{planId}
POST /operations/{planId}/execute
POST /operations/{planId}/rollback
```

握手只接受无浏览器 Origin 的首次配对；其余接口（除 `/health`）要求 `X-Agent-Token`。消息体为 `{ "text": "...", "mode": "default|plan", "context": {...} }`，响应为 SSE。

会话、操作状态和审计分别写入 Vault 的 `.obsidian-agent-data/sessions.sqlite3`、`state.sqlite3` 和 `audit.jsonl`。Skills 从 Vault 的 `skills/` 加载。
