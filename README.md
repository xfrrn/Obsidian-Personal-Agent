# Personal Knowledge Agent

一个以 Markdown 为事实源、规则优先，并通过预览和确认安全修改知识库的 Obsidian Agent。

当前已完成第一阶段的只读问答和修改计划：支持当前笔记、整个知识库查询、真实路径引用校验，以及确认后的安全执行。

## 结构

```text
apps/
  obsidian-plugin/              当前可运行 Obsidian 插件
  local-agent/                  后续本地 Agent 服务
  gateway/                      外部通讯网关
  admin-web/                    管理后台
packages/
  contracts/                    插件 / Agent / 网关协议
  domain/                       笔记、任务、项目等领域模型
  application/                  业务用例
  agent-core/                   Agent 对话、意图、规划、工具
  rule-engine/                  确定性规则
  knowledge-engine/             解析、索引、检索
  graph-engine/                 知识图谱
  integration-sdk/              扩展接口
  shared/                       共享工具
adapters/                       具体模型、存储、文件系统、插件适配器

apps/obsidian-plugin/
  src/
    bootstrap/                  插件注册入口
    features/assistant/         Agent 调度与提示词
    features/knowledge-search/  笔记候选选择
    features/operation-preview/ 修改计划与执行
    obsidian/                   Vault / Workspace 适配
    api/                        模型 API 调用
    settings/                   插件设置
    utils/                      协议和 JSON 校验
    views/assistant-view/       侧边栏对话视图
```

Agent 第一阶段设计见 [docs/agent-design.md](docs/agent-design.md)。

## 已实现

- Obsidian 侧边栏对话视图。
- 当前笔记问答。
- 全库目录筛选和候选笔记问答。
- 真实路径引用和点击跳转。
- OpenAI-compatible API 配置。
- SecretStorage API 密钥选择。
- API 地址、模型响应和引用路径校验。
- 修改计划预览、确认执行和回滚。

## 使用

1. 在插件设置中填写 OpenAI-compatible API 地址和模型名称。
2. 通过 SecretStorage 选择或创建 API 密钥；本地无认证服务可以留空。
3. 点击左侧机器人图标，选择“当前笔记”或“整个知识库”后提问。
4. 回答下方的引用可以直接打开对应笔记或标题。

## 开发环境

- Node.js 20+
- Obsidian 1.11.4+

## 开发命令

```bash
npm install
npm run check
npm test
npm run build
npm run dev
```

`npm run dev` 会监听源码并持续生成 `apps/obsidian-plugin/main.js`；`npm run build` 生成生产构建。

## 本地安装

将以下文件放入测试 Vault 的 `.obsidian/plugins/personal-knowledge-agent/`：

```text
apps/obsidian-plugin/main.js
apps/obsidian-plugin/manifest.json
apps/obsidian-plugin/styles.css
```

重新加载 Obsidian 后，在社区插件设置中启用 `Personal Knowledge Agent`。
开发和测试必须使用独立测试 Vault，不直接操作主知识库。
