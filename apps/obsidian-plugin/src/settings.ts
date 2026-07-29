import { App, Modal, Notice, PluginSettingTab, Setting, SettingGroup } from "obsidian";
import type { ButtonComponent, DropdownComponent, TextComponent } from "obsidian";
import type CodeXAgentPlugin from "./main";
import type { ThemeMode } from "./theme";
import { agentPortFromUrl } from "./url";

declare const require: ((id: string) => unknown) | undefined;

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

export interface AgentSkill {
  name: string;
  description: string;
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
    this.containerEl.addClass("pka-settings-page");
    const next = { ...this.agentPlugin.settings };
    let apiKey = this.agentPlugin.getApiKey();
    const agentPort = agentPortFromUrl(next.agentUrl);
    next.agentUrl = `http://127.0.0.1:${agentPort}`;
    const originalSettings = JSON.stringify(next);
    const originalApiKey = apiKey;
    const needsInitialSave = !next.configured;
    let modelDropdown: DropdownComponent | null = null;
    let workspaceText: TextComponent | null = null;
    let sessionDbText: TextComponent | null = null;
    let actionLabel: HTMLElement | null = null;
    let discardButton: ButtonComponent | null = null;
    let saveButton: ButtonComponent | null = null;

    const updateDirtyState = () => {
      const changed = JSON.stringify(next) !== originalSettings || apiKey !== originalApiKey;
      if (actionLabel) actionLabel.textContent = changed ? "配置已修改" : needsInitialSave ? "配置尚未保存" : "配置未修改";
      discardButton?.setDisabled(!changed);
      saveButton?.setDisabled(!needsInitialSave && !changed);
    };

    const renderModels = (models: readonly string[]) => {
      const options = Array.from(new Set(models.filter(Boolean)));
      modelDropdown?.selectEl.empty();
      if (!options.length) {
        modelDropdown?.addOption("", "请先获取模型");
        modelDropdown?.setValue("");
        modelDropdown?.setDisabled(true);
        next.model = "";
        updateDirtyState();
        return;
      }
      for (const model of options) modelDropdown?.addOption(model, model);
      if (!options.includes(next.model)) next.model = options[0];
      modelDropdown?.setValue(next.model);
      modelDropdown?.setDisabled(false);
      updateDirtyState();
    };

    const header = this.containerEl.createDiv({ cls: "pka-settings-header" });
    header.createEl("h1", { text: "Personal Knowledge Agent" });
    header.createEl("p", { text: "配置 Agent 服务、模型连接、执行权限和数据存储。" });

    const serviceGroup = new SettingGroup(this.containerEl).setHeading("Agent 服务").addClass("pka-settings-group");
    serviceGroup.addSetting((setting) => setting
      .setName("Agent 启动端口")
      .setDesc("连接和自动启动内置 Agent 使用同一端口。")
      .addText((text) => text
        .setPlaceholder("8000")
        .setValue(agentPort)
        .onChange((value) => {
          next.agentUrl = `http://127.0.0.1:${value.trim()}`;
          updateDirtyState();
        })));
    serviceGroup.addSetting((setting) => {
      setting.setName("服务地址");
      setting.controlEl.createSpan({ cls: "pka-settings-value", text: "http://127.0.0.1" });
    });
    serviceGroup.addSetting((setting) => {
      setting.setName("服务状态");
      const status = setting.controlEl.createSpan({ cls: "pka-service-status", text: "检查中…" });
      status.dataset.state = "checking";
      void this.agentPlugin.isAgentRunning().then((running) => {
        status.textContent = running ? "运行中" : "未运行";
        status.dataset.state = running ? "running" : "stopped";
      });
    });

    const modelGroup = new SettingGroup(this.containerEl).setHeading("模型配置").addClass("pka-settings-group");
    modelGroup.addSetting((setting) => setting
      .setName("模型 API 地址")
      .addText((text) => text.setValue(next.apiBaseUrl).onChange((value) => {
        next.apiBaseUrl = value;
        renderModels([]);
      }))
      .addButton((button) => button
        .setButtonText("获取模型")
        .onClick(async () => {
          button.setDisabled(true).setButtonText("获取中…");
          try {
            renderModels(await this.agentPlugin.fetchModels(next.apiBaseUrl, apiKey));
            new Notice("模型列表已更新。");
          } catch (error) {
            new Notice(error instanceof Error ? error.message : "无法获取模型列表。");
          } finally {
            button.setDisabled(false).setButtonText("获取模型");
          }
        })));
    modelGroup.addSetting((setting) => setting
      .setName("模型")
      .addDropdown((dropdown) => {
        modelDropdown = dropdown.onChange((value) => {
          next.model = value;
          updateDirtyState();
        });
        renderModels(next.model ? [next.model] : []);
      }));
    modelGroup.addSetting((setting) => setting
      .setName("API Key")
      .setDesc("保存到 Obsidian SecretStorage，不写入 data.json。")
      .addText((text) => {
        text.inputEl.type = "password";
        text.setValue(apiKey).onChange((value) => {
          apiKey = value;
          updateDirtyState();
        });
      }));

    const dataGroup = new SettingGroup(this.containerEl).setHeading("工作区与数据").addClass("pka-settings-group");
    dataGroup.addSetting((setting) => setting
      .setName("Agent 工作区")
      .setDesc("Agent 可以读取和修改的 Vault 根目录。")
      .addText((text) => {
        workspaceText = text;
        text.setValue(next.workspace).onChange((value) => {
          next.workspace = value;
          text.inputEl.title = value;
          updateDirtyState();
        });
        text.inputEl.title = next.workspace;
      })
      .addButton((button) => button.setButtonText("浏览").onClick(async () => {
        try {
          const folder = await chooseFolder(next.workspace);
          if (!folder) return;
          next.workspace = folder;
          workspaceText?.setValue(folder);
          if (workspaceText) workspaceText.inputEl.title = folder;
          updateDirtyState();
        } catch (error) {
          new Notice(error instanceof Error ? error.message : "无法选择 Agent 工作区。");
        }
      })));
    dataGroup.addSetting((setting) => setting
      .setName("会话数据库")
      .setDesc("Skills 保存在数据库同目录；留空时使用 Agent 默认路径。")
      .addText((text) => {
        sessionDbText = text;
        text.setPlaceholder("未选择").setValue(next.sessionDbPath);
        text.inputEl.readOnly = true;
        text.inputEl.title = next.sessionDbPath;
      })
      .addButton((button) => button.setButtonText("浏览").onClick(async () => {
        try {
          const path = await chooseSessionDbPath(next.sessionDbPath, next.workspace);
          if (!path) return;
          next.sessionDbPath = path;
          sessionDbText?.setValue(path);
          if (sessionDbText) sessionDbText.inputEl.title = path;
          updateDirtyState();
        } catch (error) {
          new Notice(error instanceof Error ? error.message : "无法选择会话数据库文件夹。");
        }
      })));
    dataGroup.addSetting((setting) => setting
      .setName("长期记忆")
      .setDesc("查看 Agent 当前已经合并的跨会话长期记忆。")
      .addButton((button) => button.setButtonText("查看").onClick(async () => {
        button.setDisabled(true).setButtonText("读取中…");
        try {
          const modal = new Modal(this.app).setTitle("当前长期记忆");
          modal.contentEl.createEl("pre", {
            cls: "pka-memory-content",
            text: await this.agentPlugin.getLongTermMemory() || "暂无长期记忆。"
          });
          modal.open();
        } catch (error) {
          new Notice(error instanceof Error ? error.message : "无法读取长期记忆。");
        } finally {
          button.setDisabled(false).setButtonText("查看");
        }
      })));

    const securityGroup = new SettingGroup(this.containerEl).setHeading("执行与安全").addClass("pka-settings-group");
    securityGroup.addSetting((setting) => setting
      .setName("沙盒模式")
      .addDropdown((dropdown) => dropdown
        .addOptions({
          "read-only": "只读",
          "workspace-write": "工作区写入",
          "danger-full-access": "完全访问"
        })
        .setValue(next.sandboxMode)
        .onChange((value) => {
          next.sandboxMode = value as SandboxMode;
          updateDirtyState();
        })));
    securityGroup.addSetting((setting) => setting
      .setName("审批策略")
      .addDropdown((dropdown) => dropdown
        .addOptions({ "on-request": "按需审批", never: "从不询问" })
        .setValue(next.approvalPolicy)
        .onChange((value) => {
          next.approvalPolicy = value as ApprovalPolicy;
          updateDirtyState();
        })));
    securityGroup.addSetting((setting) => setting
      .setName("启用 Shell 工具")
      .setDesc("开启后可执行本地命令；高风险操作仍受沙盒和审批策略限制。")
      .addToggle((toggle) => toggle.setValue(next.shellEnabled).onChange((value) => {
        next.shellEnabled = value;
        updateDirtyState();
      })));

    const appearanceGroup = new SettingGroup(this.containerEl).setHeading("外观").addClass("pka-settings-group");
    appearanceGroup.addSetting((setting) => setting
      .setName("界面主题")
      .setDesc("默认跟随 Obsidian，也可固定为亮色或暗色。")
      .addDropdown((dropdown) => dropdown
        .addOptions({ system: "跟随 Obsidian", light: "亮色", dark: "暗色" })
        .setValue(next.themeMode)
        .onChange((value) => {
          next.themeMode = value as ThemeMode;
          updateDirtyState();
        })));

    const skillGroup = new SettingGroup(this.containerEl).setHeading("技能（Skills）").addClass("pka-settings-group");
    const skillList = skillGroup.listEl.createDiv({ cls: "pka-skill-list" });
    const folderInput = this.containerEl.createEl("input", { type: "file" });
    folderInput.multiple = true;
    folderInput.hidden = true;
    folderInput.setAttribute("webkitdirectory", "");
    const importSetting = new Setting(skillGroup.listEl)
      .setName("导入 Skill")
      .setDesc("选择根目录含 SKILL.md 的文件夹；不会覆盖同名 Skill。")
      .addButton((button) => button.setButtonText("导入 Skill").onClick(() => folderInput.click()));
    folderInput.addEventListener("change", async () => {
      const files = Array.from(folderInput.files ?? []);
      folderInput.value = "";
      if (!files.length) return;
      importSetting.setDisabled(true);
      try {
        const skill = await this.agentPlugin.importSkill(files);
        new Notice(`已导入 Skill：${skill.name}`);
        await this.renderSkills(skillList);
      } catch (error) {
        new Notice(error instanceof Error ? error.message : "无法导入 Skill。");
      } finally {
        importSetting.setDisabled(false);
      }
    });
    void this.renderSkills(skillList);

    const actionBar = this.containerEl.createDiv({ cls: "pka-settings-actions" });
    new Setting(actionBar)
      .setName("配置未修改")
      .addButton((button) => {
        discardButton = button.setButtonText("放弃更改").onClick(() => this.display());
      })
      .addButton((button) => {
        saveButton = button.setCta().setButtonText("保存并应用").onClick(async () => {
          if (await this.agentPlugin.applySettings(next, apiKey)) this.display();
        });
      });
    actionLabel = actionBar.querySelector(".setting-item-name");
    updateDirtyState();
  }

  private async renderSkills(container: HTMLElement): Promise<void> {
    container.empty();
    new Setting(container).setName("正在读取 Skills…");
    try {
      const skills = await this.agentPlugin.listSkills();
      container.empty();
      if (!skills.length) {
        new Setting(container).setName("尚未导入 Skill").setDesc("导入后会保存在 sessions.db 同目录的 skills/ 文件夹。");
        return;
      }
      for (const skill of skills) new Setting(container).setName(skill.name).setDesc(skill.description);
    } catch (error) {
      container.empty();
      new Setting(container)
        .setName("无法读取 Skills")
        .setDesc(error instanceof Error ? error.message : "Agent 暂时不可用。");
    }
  }
}

async function chooseSessionDbPath(sessionDbPath: string, workspace: string): Promise<string | null> {
  const folder = await chooseFolder(sessionDbPath.trim() ? parentPath(sessionDbPath) : workspace.trim());
  return folder ? defaultSessionDbPath(folder) : null;
}

async function chooseFolder(defaultPath: string): Promise<string | null> {
  const dialog = electronDialog();
  if (!dialog) throw new Error("当前环境不支持选择文件夹。");
  const result = await dialog.showOpenDialog({
    properties: ["openDirectory", "createDirectory"],
    ...(defaultPath ? { defaultPath } : {})
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
}

interface ElectronDialog {
  showOpenDialog(options: {
    properties: string[];
    defaultPath?: string;
  }): Promise<{ canceled: boolean; filePaths: string[] }>;
}

function electronDialog(): ElectronDialog | null {
  if (typeof require !== "function") return null;
  try {
    const electron = require("electron") as {
      dialog?: ElectronDialog;
      remote?: { dialog?: ElectronDialog };
    };
    return electron.remote?.dialog ?? electron.dialog ?? null;
  } catch {
    return null;
  }
}

function defaultSessionDbPath(folder: string): string {
  const trimmed = folder.trim().replace(/[\\/]+$/, "");
  const separator = trimmed.includes("\\") || /^[A-Za-z]:/.test(trimmed) ? "\\" : "/";
  return `${trimmed}${separator}sessions.db`;
}

function parentPath(path: string): string {
  const trimmed = path.trim().replace(/[\\/]+$/, "");
  const index = Math.max(trimmed.lastIndexOf("\\"), trimmed.lastIndexOf("/"));
  if (index < 0) return trimmed;
  if (index === 0) return trimmed.slice(0, 1);
  if (index === 2 && /^[A-Za-z]:/.test(trimmed)) return trimmed.slice(0, 3);
  return trimmed.slice(0, index);
}
