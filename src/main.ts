import { Notice, Plugin } from "obsidian";

export default class PersonalKnowledgeAgentPlugin extends Plugin {
  onload(): void {
    this.addCommand({
      id: "open-personal-knowledge-agent",
      name: "打开个人知识库 Agent",
      callback: () => new Notice("Personal Knowledge Agent 已加载")
    });
  }
}

