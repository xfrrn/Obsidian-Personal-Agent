import {
  App,
  PluginSettingTab,
  Setting
} from "obsidian";
import { callModel } from "../api/model-client";
import type PersonalKnowledgeAgentPlugin from "../main";

export interface AgentSettings {
  provider: string;
  apiBaseUrl: string;
  model: string;
  secretId: string;
}

interface ProviderPreset {
  id: string;
  name: string;
  apiBaseUrl: string;
  models: string[];
}

export const PROVIDERS: ProviderPreset[] = [
  {
    id: "deepseek",
    name: "DeepSeek",
    apiBaseUrl: "https://api.deepseek.com",
    models: ["deepseek-v4-flash", "deepseek-v4-pro"]
  },
  {
    id: "custom",
    name: "自定义 OpenAI-compatible",
    apiBaseUrl: "https://api.openai.com/v1",
    models: []
  }
];

export const DEFAULT_SETTINGS: AgentSettings = {
  provider: "deepseek",
  apiBaseUrl: "https://api.deepseek.com",
  model: "deepseek-v4-flash",
  secretId: "personal-knowledge-agent-api-key"
};

export function providerById(id: string): ProviderPreset {
  return PROVIDERS.find((provider) => provider.id === id) ?? PROVIDERS[0];
}

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
    const provider = providerById(this.agentPlugin.settings.provider);

    new Setting(containerEl)
      .setName("供应商")
      .setDesc("DeepSeek 默认使用官方 OpenAI-compatible API。")
      .addDropdown((dropdown) => {
        for (const item of PROVIDERS) {
          dropdown.addOption(item.id, item.name);
        }
        dropdown
          .setValue(provider.id)
          .onChange(async (value) => {
            const next = providerById(value);
            this.agentPlugin.settings.provider = next.id;
            this.agentPlugin.settings.apiBaseUrl = next.apiBaseUrl;
            if (next.models.length) {
              this.agentPlugin.settings.model = next.models[0];
            }
            await this.agentPlugin.saveSettings();
            this.display();
          });
      });

    if (provider.id === "custom") {
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
    } else {
      new Setting(containerEl)
        .setName("API Base URL")
        .setDesc(provider.apiBaseUrl);
    }

    const modelSetting = new Setting(containerEl)
      .setName("模型")
      .setDesc(provider.models.length ? "选择当前供应商支持的模型。" : "填写服务端实际支持的模型名称。");
    if (provider.models.length) {
      modelSetting.addDropdown((dropdown) => {
        for (const model of provider.models) {
          dropdown.addOption(model, model);
        }
        dropdown
          .setValue(provider.models.includes(this.agentPlugin.settings.model)
            ? this.agentPlugin.settings.model
            : provider.models[0])
          .onChange(async (value) => {
            this.agentPlugin.settings.model = value;
            await this.agentPlugin.saveSettings();
          });
      });
    } else {
      modelSetting.addText((text) =>
        text
          .setPlaceholder("model-name")
          .setValue(this.agentPlugin.settings.model)
          .onChange(async (value) => {
            this.agentPlugin.settings.model = value.trim();
            await this.agentPlugin.saveSettings();
          })
      );
    }

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

    const testSetting = new Setting(containerEl)
      .setName("连接测试")
      .setDesc("使用当前供应商、模型和密钥发送一次最小请求。")
      .addButton((button) =>
        button
          .setButtonText("测试连接")
          .onClick(async () => {
            button.setDisabled(true).setButtonText("测试中...");
            const startedAt = performance.now();
            testStatusEl.setText("测试中...");
            testStatusEl.removeClass("is-success", "is-error");
            try {
              await withTimeout(
                callModel(this.app, this.agentPlugin.settings, [
                  { role: "system", content: "只返回 ok。" },
                  { role: "user", content: "ping" }
                ]),
                12_000
              );
              testStatusEl.setText(`连接成功。耗时 ${elapsedMs(startedAt)} ms。`);
              testStatusEl.addClass("is-success");
            } catch (error) {
              testStatusEl.setText(`${error instanceof Error ? error.message : "连接失败。"} 耗时 ${elapsedMs(startedAt)} ms。`);
              testStatusEl.addClass("is-error");
            } finally {
              button.setDisabled(false).setButtonText("测试连接");
            }
          })
      );
    const testStatusEl = containerEl.createDiv({ cls: "pka-setting-status" });
    testSetting.settingEl.insertAdjacentElement("afterend", testStatusEl);
  }
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_resolve, reject) => {
      setTimeout(() => reject(new Error("连接测试超时，请稍后重试或直接使用对话验证。")), ms);
    })
  ]);
}

function elapsedMs(startedAt: number): number {
  return Math.round(performance.now() - startedAt);
}
