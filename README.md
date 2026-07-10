# Personal Knowledge Agent

一个以 Markdown 为事实源、规则优先，并通过预览和确认安全修改知识库的 Obsidian Agent。

当前处于第一版开发阶段。首版范围以项目设计文档中的《Obsidian 个人知识库 Agent 第一版开发方案》为准。

## 开发环境

- Node.js 20+
- Obsidian 1.11.4+

## 开发命令

```bash
npm install
npm run check
npm run build
npm run dev
```

`npm run dev` 会监听源码并持续生成 `main.js`；`npm run build` 生成生产构建。

## 本地安装

将以下文件放入测试 Vault 的 `.obsidian/plugins/personal-knowledge-agent/`：

```text
main.js
manifest.json
```

重新加载 Obsidian 后，在社区插件设置中启用 `Personal Knowledge Agent`。

开发和测试必须使用独立测试 Vault，不直接操作主知识库。
