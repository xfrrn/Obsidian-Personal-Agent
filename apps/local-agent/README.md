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
GET  /identity
GET  /operations/{planId}
POST /handshake
POST /chat
POST /policy
POST /operations
POST /operations/{planId}/execute
POST /operations/{planId}/rollback
```

`/handshake` 只允许无浏览器 Origin 的首次配对；配对后需重启 local-agent 才能重新绑定。
`/tools`、`/identity` 和 `/chat` 都需要握手返回的 `X-Agent-Token`。

`POST /chat` 示例：

```json
{
  "userInput": "查询任务",
  "conversationId": "local-test"
}
```

OperationPlan、执行结果和回滚快照保存在 Vault 的
`.obsidian-agent-data/state.sqlite3`，审计日志写入
`.obsidian-agent-data/audit.jsonl`。执行器支持创建笔记、精确替换、移动笔记、
更新 Frontmatter 和追加任务；插件命令调用继续由 Obsidian 插件执行。

执行模式可通过插件设置或环境变量配置：

```powershell
$env:OBSIDIAN_AGENT_EXECUTION_MODE="confirm_all" # risk_based / unattended
```

- `confirm_all`：所有写入都需要确认。
- `risk_based`：白名单内的低风险写入可以自动执行。
- `unattended`：只有可信自动化触发的白名单低风险写入可以自动执行。

中高风险操作始终确认；撤销前会再次核对文件哈希，避免覆盖用户后续修改。
