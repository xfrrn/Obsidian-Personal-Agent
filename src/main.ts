import { Plugin } from "obsidian";
import { askAgent, judgeIntent, QueryScope } from "./agent";
import { AssistantView, AGENT_VIEW_TYPE } from "./assistant-view";
import {
  buildOperationPlan,
  executeOperationPlan,
  OperationPlan
} from "./operations";
import { AgentAnswer, AgentIntent } from "./protocol";
import {
  AgentSettings,
  AgentSettingTab,
  DEFAULT_SETTINGS
} from "./settings";

export default class PersonalKnowledgeAgentPlugin extends Plugin {
  settings!: AgentSettings;

  async onload(): Promise<void> {
    await this.loadSettings();
    this.registerView(
      AGENT_VIEW_TYPE,
      (leaf) => new AssistantView(leaf, this)
    );
    this.addSettingTab(new AgentSettingTab(this.app, this));
    this.addRibbonIcon("bot", "打开个人知识库 Agent", () => {
      void this.activateView();
    });
    this.addCommand({
      id: "open-personal-knowledge-agent",
      name: "打开个人知识库 Agent",
      callback: () => this.activateView()
    });
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

  private async activateView(): Promise<void> {
    await this.app.workspace.ensureSideLeaf(AGENT_VIEW_TYPE, "right", {
      active: true,
      reveal: true
    });
  }

  private async loadSettings(): Promise<void> {
    const data: unknown = await this.loadData();
    const value = typeof data === "object" && data !== null
      ? data as Record<string, unknown>
      : {};
    this.settings = {
      apiBaseUrl: typeof value.apiBaseUrl === "string"
        ? value.apiBaseUrl
        : DEFAULT_SETTINGS.apiBaseUrl,
      model: typeof value.model === "string"
        ? value.model
        : DEFAULT_SETTINGS.model,
      secretId: typeof value.secretId === "string" && value.secretId
        ? value.secretId
        : DEFAULT_SETTINGS.secretId
    };
  }
}
