import { App, PluginSettingTab, Setting } from "obsidian";
import type CodeXAgentPlugin from "./main";
import type { ThemeMode } from "./theme";

export type SandboxMode = "read-only" | "workspace-write" | "danger-full-access";
export type ApprovalPolicy = "never" | "on-request";

export interface AgentSettings {
  agentUrl: string;
  apiBaseUrl: string;
  model: string;
  workspace: string;
  sandboxMode: SandboxMode;
  approvalPolicy: ApprovalPolicy;
  shellEnabled: boolean;
  sessionDbPath: string;
  themeMode: ThemeMode;
  configured: boolean;
}

export const DEFAULT_SETTINGS: AgentSettings = {
  agentUrl: "http://127.0.0.1:8000",
  apiBaseUrl: "https://api.openai.com/v1",
  model: "gpt-4.1-mini",
  workspace: "",
  sandboxMode: "workspace-write",
  approvalPolicy: "on-request",
  shellEnabled: false,
  sessionDbPath: "",
  themeMode: "system",
  configured: false
};

export class AgentSettingTab extends PluginSettingTab {
  constructor(app: App, private readonly agentPlugin: CodeXAgentPlugin) {
    super(app, agentPlugin);
  }

  display(): void {
    this.containerEl.empty();
    const next = { ...this.agentPlugin.settings };
    let apiKey = this.agentPlugin.getApiKey();

    new Setting(this.containerEl)
      .setName("Agent 启动地址")
      .setDesc("同时用于连接和自动启动内置 Agent；仅允许本机地址，例如 http://127.0.0.1:8000。")
      .addText((text) => text
        .setPlaceholder("http://127.0.0.1:8000")
        .setValue(next.agentUrl)
        .onChange((value) => { next.agentUrl = value; }));
    new Setting(this.containerEl)
      .setName("模型 API 地址")
      .addText((text) => text.setValue(next.apiBaseUrl).onChange((value) => { next.apiBaseUrl = value; }));
    new Setting(this.containerEl)
      .setName("模型")
      .addText((text) => text.setValue(next.model).onChange((value) => { next.model = value; }));
    new Setting(this.containerEl)
      .setName("API Key")
      .setDesc("保存到 Obsidian SecretStorage，不写入 data.json。")
      .addText((text) => {
        text.inputEl.type = "password";
        text.setValue(apiKey).onChange((value) => { apiKey = value; });
      });
    new Setting(this.containerEl)
      .setName("Agent 工作区")
      .setDesc("通常填写当前 Vault 的绝对路径。")
      .addText((text) => text.setValue(next.workspace).onChange((value) => { next.workspace = value; }));
    new Setting(this.containerEl)
      .setName("沙盒模式")
      .addDropdown((dropdown) => dropdown
        .addOptions({
          "read-only": "只读",
          "workspace-write": "工作区写入",
          "danger-full-access": "完全访问"
        })
        .setValue(next.sandboxMode)
        .onChange((value) => { next.sandboxMode = value as SandboxMode; }));
    new Setting(this.containerEl)
      .setName("审批策略")
      .addDropdown((dropdown) => dropdown
        .addOptions({ "on-request": "按需审批", never: "从不询问" })
        .setValue(next.approvalPolicy)
        .onChange((value) => { next.approvalPolicy = value as ApprovalPolicy; }));
    new Setting(this.containerEl)
      .setName("启用 Shell 工具")
      .setDesc("启用 exec_command 和 write_stdin。")
      .addToggle((toggle) => toggle.setValue(next.shellEnabled).onChange((value) => { next.shellEnabled = value; }));
    new Setting(this.containerEl)
      .setName("会话数据库")
      .setDesc("留空时继续使用 Agent 当前路径。")
      .addText((text) => text.setValue(next.sessionDbPath).onChange((value) => { next.sessionDbPath = value; }));
    new Setting(this.containerEl)
      .setName("界面主题")
      .setDesc("默认跟随 Obsidian，也可固定为亮色或暗色。")
      .addDropdown((dropdown) => dropdown
        .addOptions({ system: "跟随 Obsidian", light: "亮色", dark: "暗色" })
        .setValue(next.themeMode)
        .onChange((value) => { next.themeMode = value as ThemeMode; }));
    new Setting(this.containerEl)
      .addButton((button) => button
        .setCta()
        .setButtonText("保存并应用")
        .onClick(async () => this.agentPlugin.applySettings(next, apiKey)));
  }
}
