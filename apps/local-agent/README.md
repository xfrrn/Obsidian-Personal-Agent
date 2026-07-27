# CodeX-Agent Local Service

## 启动 Web 控制台

```powershell
python -m pip install -e apps/local-agent
$env:OPENAI_API_KEY="..."
$env:OPENAI_MODEL="model-name"
$env:AGENT_WORKSPACE="D:\Vault"
python -m agent.web.server --host 127.0.0.1 --port 8000
```

终端模式使用 `python -m agent.cli.main`。模型地址、沙盒、Shell 和会话数据库分别由 `OPENAI_BASE_URL`、`AGENT_SANDBOX_*`、`AGENT_ENABLE_SHELL` 和 `AGENT_SESSION_DB` 配置。

Web 服务提供多会话、SSE 消息流、审批、权限和指标接口。默认仅监听本机，但当前没有请求令牌；不要绑定到公网地址。
