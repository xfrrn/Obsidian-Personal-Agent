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

意图识别默认使用关键词规则；插件通过 `/handshake` 会把设置页里的
OpenAI-compatible API 地址、模型和密钥传给 Local Agent，之后优先用 LLM function call
识别意图。调用失败、超时或返回无效 JSON 时自动回退到关键词规则。

手动调试时也可以用环境变量启用同一能力：

```powershell
$env:OBSIDIAN_AGENT_INTENT_LLM_BASE_URL="https://api.example.com/v1"
$env:OBSIDIAN_AGENT_INTENT_LLM_MODEL="model-name"
$env:OBSIDIAN_AGENT_INTENT_LLM_API_KEY="..." # 可选
```

当前注册的只读业务 tools：

```text
search_notes              结构化笔记搜索
read_note                 单篇或受控批量读取
list_tasks                Tasks 语法查询
inspect_note              当前笔记规范和链接检查
analyze_project           项目总览、任务和缺失文档
check_vault_health        全库健康报告
list_tags                 标签统计
find_related_notes        确定性关联推荐
find_duplicates           重复和高相似笔记检测
list_rules                查看规则
evaluate_rules            规则试运行
extract_task_candidates   潜在任务提取
```

可选自定义规则放在 `.obsidian-agent-data/rules.json`；文件必须是 JSON 对象数组，
相同 `id` 会覆盖内置规则。P3 外部输入、通讯、第三方插件 capability 和知识图谱尚未接入。
