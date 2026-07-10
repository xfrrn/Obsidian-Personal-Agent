import {
  App,
  PluginSettingTab,
  Setting
} from "obsidian";
import type PersonalKnowledgeAgentPlugin from "./main";

export interface AgentSettings {
  apiBaseUrl: string;
  model: string;
  secretId: string;
}

export const DEFAULT_SETTINGS: AgentSettings = {
  apiBaseUrl: "https://api.openai.com/v1",
  model: "",
  secretId: "personal-knowledge-agent-api-key"
};

export class AgentSettingTab extends PluginSettingTab {
  constructor(
    app: App,
    private readonly agentPlugin: PersonalKnowledgeAgentPlugin
  ) {
    super(app, agentPlugin);
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();

    new Setting(containerEl)
      .setName("API Base URL")
      .setDesc("OpenAI-compatible API 地址；普通 HTTP 只允许本机地址。")
      .addText((text) =>
        text
          .setPlaceholder("https://api.openai.com/v1")
          .setValue(this.agentPlugin.settings.apiBaseUrl)
          .onChange(async (value) => {
            this.agentPlugin.settings.apiBaseUrl = value.trim();
            await this.agentPlugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName("模型")
      .setDesc("填写服务端实际支持的模型名称。")
      .addText((text) =>
        text
          .setPlaceholder("model-name")
          .setValue(this.agentPlugin.settings.model)
          .onChange(async (value) => {
            this.agentPlugin.settings.model = value.trim();
            await this.agentPlugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName("API 密钥")
      .setDesc("从 Obsidian SecretStorage 中选择；本地无认证服务可以留空。")
      .addText((text) =>
        text
          .setPlaceholder("sk-...")
          .setValue(this.app.secretStorage.getSecret(this.agentPlugin.settings.secretId) ?? "")
          .onChange(async (value) => {
            this.app.secretStorage.setSecret(
              this.agentPlugin.settings.secretId,
              value.trim()
            );
            await this.agentPlugin.saveSettings();
          })
      );

    const keyInput = containerEl.querySelector<HTMLInputElement>(
      ".setting-item:last-child input"
    );
    if (keyInput) keyInput.type = "password";
  }
}
