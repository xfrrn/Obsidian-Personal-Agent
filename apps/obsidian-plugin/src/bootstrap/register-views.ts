import type PersonalKnowledgeAgentPlugin from "../main";
import {
  AGENT_VIEW_TYPE,
  AssistantView
} from "../views/assistant-view/assistant-view";

export function registerViews(plugin: PersonalKnowledgeAgentPlugin): void {
  plugin.registerView(
    AGENT_VIEW_TYPE,
    (leaf) => new AssistantView(leaf, plugin)
  );
}
