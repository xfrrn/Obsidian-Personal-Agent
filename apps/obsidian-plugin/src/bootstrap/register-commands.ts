import type PersonalKnowledgeAgentPlugin from "../main";
import { openAssistantView } from "../obsidian/workspace-controller";

export function registerCommands(plugin: PersonalKnowledgeAgentPlugin): void {
  plugin.addRibbonIcon("bot", "打开个人知识库 Agent", () => {
    void openAssistantView(plugin.app);
  });
  plugin.addCommand({
    id: "open-personal-knowledge-agent",
    name: "打开个人知识库 Agent",
    callback: () => openAssistantView(plugin.app)
  });
}
