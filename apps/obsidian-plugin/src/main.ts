import { Notice, Plugin, requestUrl } from "obsidian";
import { AgentSettingTab, AgentSettings, ApprovalPolicy, DEFAULT_SETTINGS, SandboxMode } from "./settings";
import { normalizeAgentUrl, normalizeApiBaseUrl } from "./url";
import { CODEX_AGENT_VIEW_TYPE, CodeXAgentView } from "./codex-agent-view";
import { isThemeMode, ThemeMode } from "./bridge";

const API_KEY_SECRET_ID = "personal-knowledge-agent-api-key";

export default class CodeXAgentPlugin extends Plugin {
  settings!: AgentSettings;
  private apiKey = "";

  async onload(): Promise<void> {
    await this.loadSettings();
    this.registerView(
      CODEX_AGENT_VIEW_TYPE,
      (leaf) => new CodeXAgentView(
        leaf,
        () => this.settings.agentUrl,
        () => this.syncAgentSettings(),
        () => ({ sandboxMode: this.settings.sandboxMode, themeMode: this.settings.themeMode }),
        (settings) => this.persistPanelSettings(settings)
      )
    );
    this.addRibbonIcon("bot", "打开 CodeX Agent", () => void this.activateView());
    this.addCommand({
      id: "open-codex-agent",
      name: "打开 CodeX Agent",
      callback: () => void this.activateView()
    });
    this.addSettingTab(new AgentSettingTab(this.app, this));
    void this.syncAgentSettings().catch(() => undefined);
  }

  onunload(): void {
    this.app.workspace.detachLeavesOfType(CODEX_AGENT_VIEW_TYPE);
  }

  getApiKey(): string {
    return this.apiKey;
  }

  async persistPanelSettings(settings: { sandboxMode?: SandboxMode; themeMode?: ThemeMode }): Promise<void> {
    this.settings = { ...this.settings, ...settings };
    await this.saveData(this.settings);
  }

  async applySettings(value: AgentSettings, apiKey: string): Promise<void> {
    try {
      const workspace = value.workspace.trim();
      const model = value.model.trim();
      if (!workspace || !model) throw new Error("模型和 Agent 工作区不能为空。");
      this.settings = {
        ...value,
        agentUrl: normalizeAgentUrl(value.agentUrl),
        apiBaseUrl: normalizeApiBaseUrl(value.apiBaseUrl),
        model,
        workspace,
        sessionDbPath: value.sessionDbPath.trim(),
        configured: true
      };
      this.apiKey = apiKey.trim();
      this.app.secretStorage.setSecret(API_KEY_SECRET_ID, this.apiKey);
      await this.saveData(this.settings);
    } catch (error) {
      new Notice(error instanceof Error ? error.message : "无法保存 Agent 配置。");
      return;
    }
    try {
      await this.syncAgentSettings(true);
      for (const leaf of this.app.workspace.getLeavesOfType(CODEX_AGENT_VIEW_TYPE)) {
        if (leaf.view instanceof CodeXAgentView) void leaf.view.refresh();
      }
      new Notice("Agent 配置已保存并应用。");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Agent 暂时不可用。";
      new Notice(`配置已保存，但尚未应用：${message}`);
    }
  }

  private async syncAgentSettings(includeEmptyApiKey = false): Promise<void> {
    const body: Record<string, unknown> = this.settings.configured ? {
        base_url: this.settings.apiBaseUrl,
        model: this.settings.model,
        workspace: this.settings.workspace,
        sandbox_mode: this.settings.sandboxMode,
        approval_policy: this.settings.approvalPolicy,
        shell_enabled: this.settings.shellEnabled,
        session_db_path: this.settings.sessionDbPath,
        confirmed: this.settings.sandboxMode === "danger-full-access"
      } : { workspace: this.settings.workspace };
    if (this.settings.configured && (this.apiKey || includeEmptyApiKey)) body.api_key = this.apiKey;
    const response = await requestUrl({
      url: `${this.settings.agentUrl}/api/config`,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      throw: false
    });
    if (response.status < 200 || response.status >= 300) {
      const payload = response.json as { error?: unknown };
      throw new Error(typeof payload?.error === "string" ? payload.error : `Agent 配置失败：HTTP ${response.status}`);
    }
  }

  private async activateView(): Promise<void> {
    await this.syncAgentSettings().catch(() => undefined);
    const existing = this.app.workspace.getLeavesOfType(CODEX_AGENT_VIEW_TYPE)[0];
    const leaf = existing ?? this.app.workspace.getRightLeaf(false);
    if (!leaf) return;
    if (!existing) await leaf.setViewState({ type: CODEX_AGENT_VIEW_TYPE, active: true });
    await this.app.workspace.revealLeaf(leaf);
  }

  private async loadSettings(): Promise<void> {
    const saved = await this.loadData() as Partial<AgentSettings> | null;
    const adapter = this.app.vault.adapter as { getBasePath?: () => string };
    const configured = saved?.configured === true;
    let agentUrl = DEFAULT_SETTINGS.agentUrl;
    let apiBaseUrl = DEFAULT_SETTINGS.apiBaseUrl;
    try {
      agentUrl = normalizeAgentUrl(typeof saved?.agentUrl === "string" ? saved.agentUrl : agentUrl);
      apiBaseUrl = normalizeApiBaseUrl(typeof saved?.apiBaseUrl === "string" ? saved.apiBaseUrl : apiBaseUrl);
    } catch {
      // 损坏的本地设置回退到安全默认值，不能阻止插件启动。
    }
    this.settings = {
      agentUrl,
      apiBaseUrl,
      model: typeof saved?.model === "string" ? saved.model : DEFAULT_SETTINGS.model,
      workspace: configured && typeof saved?.workspace === "string" && saved.workspace.trim()
        ? saved.workspace
        : adapter.getBasePath?.() ?? "",
      sandboxMode: isSandboxMode(saved?.sandboxMode) ? saved.sandboxMode : DEFAULT_SETTINGS.sandboxMode,
      approvalPolicy: isApprovalPolicy(saved?.approvalPolicy) ? saved.approvalPolicy : DEFAULT_SETTINGS.approvalPolicy,
      shellEnabled: typeof saved?.shellEnabled === "boolean" ? saved.shellEnabled : DEFAULT_SETTINGS.shellEnabled,
      sessionDbPath: typeof saved?.sessionDbPath === "string" ? saved.sessionDbPath : DEFAULT_SETTINGS.sessionDbPath,
      themeMode: isThemeMode(saved?.themeMode) ? saved.themeMode : DEFAULT_SETTINGS.themeMode,
      configured
    };
    this.apiKey = this.app.secretStorage.getSecret(API_KEY_SECRET_ID) ?? "";
  }
}

function isSandboxMode(value: unknown): value is SandboxMode {
  return value === "read-only" || value === "workspace-write" || value === "danger-full-access";
}

function isApprovalPolicy(value: unknown): value is ApprovalPolicy {
  return value === "never" || value === "on-request";
}
