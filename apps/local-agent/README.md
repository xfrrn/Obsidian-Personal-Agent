# Local Agent

本地 Agent 服务承接模型推理、工具选择、任务规划、检索、规则和后台任务。

第一版先作为模块化单体，不拆微服务；Obsidian 插件只通过 HTTP / WebSocket 调用这里。
