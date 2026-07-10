# Local Agent

本地 Agent 服务承接模型推理、工具选择、任务规划、检索、规则和后台任务。

第一版先作为模块化单体，不拆微服务；Obsidian 插件只通过 HTTP / WebSocket 调用这里。

## 运行

```powershell
$env:OBSIDIAN_AGENT_PORT="8765"
python apps/local-agent/src/main.py
```

Vault 路径由 Obsidian 插件通过 `/handshake` 自动发送；手动调试时也可以设置
`OBSIDIAN_AGENT_VAULT_ROOT` 跳过握手。

接口：

```text
GET  /health
GET  /tools
POST /handshake
POST /chat
```

`POST /chat` 示例：

```json
{
  "userInput": "查询任务",
  "conversationId": "local-test"
}
```

当前已接入文件系统只读 adapter 和内存 OperationPlan；真实写入执行器暂未接入。
