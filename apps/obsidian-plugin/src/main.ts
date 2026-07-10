import { Plugin } from "obsidian";
import { initializePlugin } from "./bootstrap/initialize-plugin";
import { askAgent, judgeIntent } from "./features/assistant/agent-loop";
import type { QueryScope } from "./features/assistant/types";
import {
  buildOperationPlan,
  executeOperationPlan,
  OperationPlan
} from "./features/operation-preview/operation-executor";
import { AGENT_VIEW_TYPE } from "./views/assistant-view/assistant-view";
import type { AgentAnswer, AgentIntent } from "./types";
import {
  AgentSettings,
  DEFAULT_SETTINGS,
  providerById
} from "./settings/settings";

export default class PersonalKnowledgeAgentPlugin extends Plugin {
  settings!: AgentSettings;

  async onload(): Promise<void> {
    await this.loadSettings();
    initializePlugin(this);
  }

  onunload(): void {
    this.app.workspace.detachLeavesOfType(AGENT_VIEW_TYPE);
  }

  ask(question: string, scope: QueryScope): Promise<AgentAnswer> {
    return askAgent(this.app, this.settings, question, scope);
  }

  intent(input: string): Promise<AgentIntent> {
    return judgeIntent(this.app, this.settings, input);
  }

  plan(request: string, scope: QueryScope): Promise<OperationPlan> {
    return buildOperationPlan(this.app, this.settings, request, scope);
  }

  executePlan(plan: OperationPlan): Promise<string[]> {
    return executeOperationPlan(this.app, plan);
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }

  private async loadSettings(): Promise<void> {
    const data: unknown = await this.loadData();
    const value = typeof data === "object" && data !== null
      ? data as Record<string, unknown>
      : {};
    const oldApiBaseUrl = typeof value.apiBaseUrl === "string"
      ? value.apiBaseUrl
      : DEFAULT_SETTINGS.apiBaseUrl;
    const providerId = typeof value.provider === "string"
      ? value.provider
      : oldApiBaseUrl.includes("api.deepseek.com")
        ? "deepseek"
        : "custom";
    const provider = providerById(providerId);
    const model = typeof value.model === "string" && value.model
      ? value.model
      : provider.models[0] ?? "";
    this.settings = {
      provider: provider.id,
      apiBaseUrl: provider.id === "custom" ? oldApiBaseUrl : provider.apiBaseUrl,
      model: provider.models.length && !provider.models.includes(model)
        ? provider.models[0]
        : model,
      secretId: typeof value.secretId === "string" && value.secretId
        ? value.secretId
        : DEFAULT_SETTINGS.secretId
    };
  }
}
