import type PersonalKnowledgeAgentPlugin from "../main";
import { AgentSettingTab } from "../settings/settings";

export function registerSettings(plugin: PersonalKnowledgeAgentPlugin): void {
  plugin.addSettingTab(new AgentSettingTab(plugin.app, plugin));
}
