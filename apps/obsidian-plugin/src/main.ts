import { Notice, Plugin } from 'obsidian';
import { AgentSettingTab, AgentSettings, DEFAULT_SETTINGS } from "./settings";
import { normalizeAgentUrl } from "./url";
import { CODEX_AGENT_VIEW_TYPE, CodeXAgentView } from "./codex-agent-view";

export default class CodeXAgentPlugin extends Plugin {
  settings!: AgentSettings;

  async onload(): Promise<void> {
    await this.loadSettings();
    this.registerView(
      CODEX_AGENT_VIEW_TYPE,
      (leaf) => new CodeXAgentView(leaf, () => this.settings.agentUrl)
    );
    this.addRibbonIcon('bot', '打开 CodeX Agent', () => void this.activateView());
    this.addCommand({
      id: 'open-codex-agent',
      name: '打开 CodeX Agent',
      callback: () => void this.activateView()
    });
    this.addSettingTab(new AgentSettingTab(this.app, this));
  }

  onunload(): void {
    this.app.workspace.detachLeavesOfType(CODEX_AGENT_VIEW_TYPE);
  }

  async setAgentUrl(value: string): Promise<void> {
    try {
      this.settings.agentUrl = normalizeAgentUrl(value);
      await this.saveData(this.settings);
      for (const leaf of this.app.workspace.getLeavesOfType(CODEX_AGENT_VIEW_TYPE)) {
        if (leaf.view instanceof CodeXAgentView) leaf.view.refresh();
      }
    } catch (error) {
      new Notice(error instanceof Error ? error.message : 'CodeX-Agent 地址无效。');
    }
  }

  private async activateView(): Promise<void> {
    const existing = this.app.workspace.getLeavesOfType(CODEX_AGENT_VIEW_TYPE)[0];
    const leaf = existing ?? this.app.workspace.getRightLeaf(false);
    if (!leaf) return;
    if (!existing) {
      await leaf.setViewState({ type: CODEX_AGENT_VIEW_TYPE, active: true });
    }
    await this.app.workspace.revealLeaf(leaf);
  }

  private async loadSettings(): Promise<void> {
    const saved: unknown = await this.loadData();
    const value = typeof saved === 'object' && saved !== null
      ? (saved as Partial<AgentSettings>).agentUrl
      : undefined;
    try {
      this.settings = { agentUrl: normalizeAgentUrl(value ?? DEFAULT_SETTINGS.agentUrl) };
    } catch {
      this.settings = { ...DEFAULT_SETTINGS };
    }
  }
}
