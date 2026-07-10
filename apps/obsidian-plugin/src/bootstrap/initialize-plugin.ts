import type PersonalKnowledgeAgentPlugin from "../main";
import { registerCommands } from "./register-commands";
import { registerSettings } from "./register-settings";
import { registerViews } from "./register-views";

export function initializePlugin(plugin: PersonalKnowledgeAgentPlugin): void {
  registerViews(plugin);
  registerSettings(plugin);
  registerCommands(plugin);
}
