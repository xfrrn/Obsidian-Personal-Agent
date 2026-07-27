import { Plugin } from "obsidian";
import { initializePlugin } from "./bootstrap/initialize-plugin";
import { AGENT_VIEW_TYPE } from "./views/assistant-view/assistant-view";
import {
  discoverLocalAgent,
  executeLocalOperationPlan,
  rollbackLocalOperationPlan,
  updateLocalAgentPolicy
} from "./api/local-agent-client";
import type { OperationPlan } from "./api/operation-plan";
import {
  AgentSettings,
  DEFAULT_SETTINGS,
  isExecutionMode,
  providerById
} from "./settings/settings";

const LOCAL_AGENT_TOKEN_SECRET_ID = "personal-knowledge-agent-local-token";

export default class PersonalKnowledgeAgentPlugin extends Plugin {
  settings!: AgentSettings;
  private autoConnectTimer: number | null = null;
  private autoConnectRunning = false;

  async onload(): Promise<void> {
    await this.loadSettings();
    initializePlugin(this);
    this.startLocalAgentAutoConnect();
  }

  onunload(): void {
    this.app.workspace.detachLeavesOfType(AGENT_VIEW_TYPE);
  }

  executePlan(plan: OperationPlan): Promise<string[]> {
    return executeLocalOperationPlan(this.settings, plan);
  }

  rollbackPlan(plan: OperationPlan): Promise<string[]> {
    return rollbackLocalOperationPlan(this.settings, plan);
  }

  async setActiveConversation(id: string): Promise<void> {
    this.settings.activeConversationId = id;
    await this.saveSettings();
  }

  private startLocalAgentAutoConnect(): void {
    const connect = () => void this.autoConnectLocalAgent();
    connect();
    this.autoConnectTimer = window.setInterval(connect, 5_000);
    this.registerInterval(this.autoConnectTimer);
  }

  private async autoConnectLocalAgent(): Promise<void> {
    if (this.autoConnectRunning) return;
    this.autoConnectRunning = true;
    try {
      const result = await discoverLocalAgent(this.app, this.settings);
      this.settings.localAgentPort = result.port;
      this.settings.localAgentToken = result.token;
      await this.saveSettings();
      await updateLocalAgentPolicy(this.app, this.settings);
      if (this.autoConnectTimer !== null) {
        window.clearInterval(this.autoConnectTimer);
        this.autoConnectTimer = null;
      }
    } catch {
      // 服务由用户单独启动；保持轮询直到可连接。
    } finally {
      this.autoConnectRunning = false;
    }
  }

  async saveSettings(): Promise<void> {
    const { localAgentToken, ...settings } = this.settings;
    if (localAgentToken) this.app.secretStorage.setSecret(LOCAL_AGENT_TOKEN_SECRET_ID, localAgentToken);
    await this.saveData(settings);
  }

  private async loadSettings(): Promise<void> {
    const data: unknown = await this.loadData();
    const value = typeof data === "object" && data !== null ? data as Record<string, unknown> : {};
    const oldApiBaseUrl = typeof value.apiBaseUrl === "string" ? value.apiBaseUrl : DEFAULT_SETTINGS.apiBaseUrl;
    const providerId = typeof value.provider === "string"
      ? value.provider
      : oldApiBaseUrl.includes("api.deepseek.com") ? "deepseek" : "custom";
    const provider = providerById(providerId);
    const model = typeof value.model === "string" && value.model ? value.model : provider.models[0] ?? "";
    this.settings = {
      provider: provider.id,
      apiBaseUrl: provider.id === "custom" ? oldApiBaseUrl : provider.apiBaseUrl,
      model: provider.models.length && !provider.models.includes(model) ? provider.models[0] : model,
      secretId: typeof value.secretId === "string" && value.secretId ? value.secretId : DEFAULT_SETTINGS.secretId,
      localAgentPort: typeof value.localAgentPort === "string" ? value.localAgentPort : DEFAULT_SETTINGS.localAgentPort,
      localAgentToken: this.app.secretStorage.getSecret(LOCAL_AGENT_TOKEN_SECRET_ID) ?? "",
      executionMode: isExecutionMode(value.executionMode) ? value.executionMode : DEFAULT_SETTINGS.executionMode,
      activeConversationId: typeof value.activeConversationId === "string" ? value.activeConversationId : ""
    };
  }
}
