import { App, PluginSettingTab, Setting } from "obsidian";
import type CodeXAgentPlugin from "./main";

export interface AgentSettings {
  agentUrl: string;
}

export const DEFAULT_SETTINGS: AgentSettings = {
  agentUrl: "http://127.0.0.1:8000"
};

export class AgentSettingTab extends PluginSettingTab {
  constructor(app: App, private readonly agentPlugin: CodeXAgentPlugin) {
    super(app, agentPlugin);
  }

  display(): void {
    this.containerEl.empty();
    let nextUrl = this.agentPlugin.settings.agentUrl;

    new Setting(this.containerEl)
      .setName("CodeX-Agent 地址")
      .setDesc("仅允许本机回环地址。模型、密钥、工作区、沙箱和工具均在 CodeX-Agent 的 .env 中配置。")
      .addText((text) => text
        .setPlaceholder(DEFAULT_SETTINGS.agentUrl)
        .setValue(nextUrl)
        .onChange((value) => { nextUrl = value; }))
      .addButton((button) => button
        .setButtonText("应用")
        .onClick(async () => this.agentPlugin.setAgentUrl(nextUrl)));
  }
}
