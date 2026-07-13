import { Plugin } from "obsidian";
import { initializePlugin } from "./bootstrap/initialize-plugin";
import { askAgent, judgeIntent } from "./features/assistant/agent-loop";
import type { ChatMessage, QueryScope } from "./features/assistant/types";
import {
  buildOperationPlan,
  executeOperationPlan,
  OperationPlan
} from "./features/operation-preview/operation-executor";
import { AGENT_VIEW_TYPE } from "./views/assistant-view/assistant-view";
import {
  executeLocalOperationPlan,
  rollbackLocalOperationPlan
} from "./api/local-agent-client";
import type { AgentAnswer, AgentIntent, AgentTraceStep } from "./types";
import {
  AgentSettings,
  DEFAULT_SETTINGS,
  isExecutionMode,
  providerById
} from "./settings/settings";

const LOCAL_AGENT_TOKEN_SECRET_ID = "personal-knowledge-agent-local-token";

export default class PersonalKnowledgeAgentPlugin extends Plugin {
  settings!: AgentSettings;

  async onload(): Promise<void> {
    await this.loadSettings();
    initializePlugin(this);
  }

  onunload(): void {
    this.app.workspace.detachLeavesOfType(AGENT_VIEW_TYPE);
  }

  ask(
    question: string,
    scope: QueryScope,
    history: ChatMessage[] = [],
    onTrace?: (step: AgentTraceStep) => void
  ): Promise<AgentAnswer> {
    return askAgent(this.app, this.settings, question, scope, history, onTrace);
  }

  intent(input: string): Promise<AgentIntent> {
    return judgeIntent(this.app, this.settings, input);
  }

  plan(request: string, scope: QueryScope): Promise<OperationPlan> {
    return buildOperationPlan(this.app, this.settings, request, scope);
  }

  executePlan(plan: OperationPlan, confirmed = true): Promise<string[]> {
    if (plan.managedBy === "local-agent") {
      return executeLocalOperationPlan(this.settings, plan, confirmed);
    }
    return executeOperationPlan(this.app, plan);
  }

  rollbackPlan(plan: OperationPlan): Promise<string[]> {
    if (plan.managedBy !== "local-agent") {
      return Promise.reject(new Error("该计划没有持久化撤销快照。"));
    }
    return rollbackLocalOperationPlan(this.settings, plan);
  }

  async saveSettings(): Promise<void> {
    const { localAgentToken, ...settings } = this.settings;
    if (localAgentToken) {
      this.app.secretStorage.setSecret(LOCAL_AGENT_TOKEN_SECRET_ID, localAgentToken);
    }
    await this.saveData(settings);
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
        : DEFAULT_SETTINGS.secretId,
      localAgentPort: typeof value.localAgentPort === "string"
        ? value.localAgentPort
        : DEFAULT_SETTINGS.localAgentPort,
      localAgentToken: this.app.secretStorage.getSecret(LOCAL_AGENT_TOKEN_SECRET_ID) ?? "",
      executionMode: isExecutionMode(value.executionMode)
        ? value.executionMode
        : DEFAULT_SETTINGS.executionMode
    };
  }
}
