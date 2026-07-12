import {
  App,
  PluginSettingTab,
  Setting
} from "obsidian";
import {
  discoverLocalAgent,
  listLocalAgentTools,
  testLocalAgent,
  type LocalAgentTool
} from "../api/local-agent-client";
import { callModel } from "../api/model-client";
import type PersonalKnowledgeAgentPlugin from "../main";

export interface AgentSettings {
  provider: string;
  apiBaseUrl: string;
  model: string;
  secretId: string;
  localAgentPort: string;
  localAgentToken: string;
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
  secretId: "personal-knowledge-agent-api-key",
  localAgentPort: "8765",
  localAgentToken: ""
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
      .setName("本地 Agent 端口")
      .setDesc("local-agent HTTP 端口；留空则只使用插件内置流程。")
      .addText((text) =>
        text
          .setPlaceholder("8765")
          .setValue(this.agentPlugin.settings.localAgentPort)
          .onChange(async (value) => {
            this.agentPlugin.settings.localAgentPort = value.trim();
            await this.agentPlugin.saveSettings();
          })
      );

    const localAgentSetting = new Setting(containerEl)
      .setName("本地 Agent 连接测试")
      .setDesc("自动发现本地 Agent，发送当前 Vault 路径，并保存访问令牌。")
      .addButton((button) =>
        button
          .setButtonText("自动连接")
          .onClick(async () => {
            button.setDisabled(true).setButtonText("连接中...");
            localAgentStatusEl.setText("连接中...");
            localAgentStatusEl.removeClass("is-success", "is-error");
            try {
              const result = await discoverLocalAgent(this.app, this.agentPlugin.settings);
              this.agentPlugin.settings.localAgentPort = result.port;
              this.agentPlugin.settings.localAgentToken = result.token;
              await this.agentPlugin.saveSettings();
              localAgentStatusEl.setText(`本地 Agent 已连接：${result.vaultRoot}`);
              localAgentStatusEl.addClass("is-success");
            } catch (error) {
              localAgentStatusEl.setText(error instanceof Error ? error.message : "本地 Agent 不可用。");
              localAgentStatusEl.addClass("is-error");
            } finally {
              button.setDisabled(false).setButtonText("自动连接");
            }
          })
      )
      .addButton((button) =>
        button
          .setButtonText("测试")
          .onClick(async () => {
            button.setDisabled(true).setButtonText("测试中...");
            localAgentStatusEl.setText("测试中...");
            localAgentStatusEl.removeClass("is-success", "is-error");
            try {
              await testLocalAgent(this.app, this.agentPlugin.settings);
              localAgentStatusEl.setText("本地 Agent 鉴权和工具接口可用。");
              localAgentStatusEl.addClass("is-success");
            } catch (error) {
              localAgentStatusEl.setText(error instanceof Error ? error.message : "本地 Agent 不可用。");
              localAgentStatusEl.addClass("is-error");
            } finally {
              button.setDisabled(false).setButtonText("测试");
            }
          })
      );
    const localAgentStatusEl = containerEl.createDiv({ cls: "pka-setting-status" });
    localAgentSetting.settingEl.insertAdjacentElement("afterend", localAgentStatusEl);

    const toolsSetting = new Setting(containerEl)
      .setName("工具展示")
      .setDesc("查看本地 Agent 当前注册的工具。")
      .addButton((button) =>
        button
          .setButtonText("刷新工具列表")
          .onClick(async () => {
            button.setDisabled(true).setButtonText("刷新中...");
            renderToolsPanel(toolsPanelEl, "loading");
            try {
              renderToolsPanel(toolsPanelEl, await listLocalAgentTools(this.agentPlugin.settings));
            } catch (error) {
              renderToolsPanel(
                toolsPanelEl,
                error instanceof Error ? error.message : "读取工具列表失败。"
              );
            } finally {
              button.setDisabled(false).setButtonText("刷新工具列表");
            }
          })
      );
    const toolsPanelEl = containerEl.createDiv({ cls: "pka-tools-panel" });
    toolsSetting.settingEl.insertAdjacentElement("afterend", toolsPanelEl);
    renderToolsPanel(toolsPanelEl, []);

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

function renderToolsPanel(
  containerEl: HTMLElement,
  toolsOrMessage: LocalAgentTool[] | "loading" | string
): void {
  containerEl.empty();
  if (toolsOrMessage === "loading") {
    containerEl.createDiv({ cls: "pka-tools-empty", text: "正在读取工具列表..." });
    return;
  }
  if (typeof toolsOrMessage === "string") {
    containerEl.createDiv({ cls: "pka-tools-empty is-error", text: toolsOrMessage });
    return;
  }
  if (!toolsOrMessage.length) {
    containerEl.createDiv({ cls: "pka-tools-empty", text: "尚未加载工具列表。" });
    return;
  }
  for (const tool of toolsOrMessage) {
    const card = containerEl.createDiv({ cls: "pka-tool-card" });
    const header = card.createDiv({ cls: "pka-tool-card-header" });
    header.createDiv({ cls: "pka-tool-name", text: tool.name });
    header.createDiv({ cls: "pka-tool-meta", text: toolMeta(tool).join(" · ") });
    card.createDiv({ cls: "pka-tool-description", text: tool.description });
    const inputs = inputNames(tool.input_schema);
    if (inputs) card.createDiv({ cls: "pka-tool-schema", text: `参数：${inputs}` });
  }
}

function toolMeta(tool: LocalAgentTool): string[] {
  return [
    tool.permission,
    tool.effect,
    tool.risk_level,
    tool.invocation_policy,
    tool.requires_confirmation ? "需确认" : undefined,
    typeof tool.timeout_seconds === "number" ? `${tool.timeout_seconds}s` : undefined
  ].filter((item): item is string => Boolean(item));
}

function inputNames(schema: Record<string, unknown> | undefined): string {
  const properties = schema?.properties;
  return isRecord(properties) ? Object.keys(properties).join(", ") : "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
