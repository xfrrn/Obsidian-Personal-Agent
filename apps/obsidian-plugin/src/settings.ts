import { App, Modal, Notice, PluginSettingTab, setIcon, Setting, SettingGroup } from "obsidian";
import type { ButtonComponent, DropdownComponent, TextComponent } from "obsidian";
import type CodeXAgentPlugin from "./main";
import {
  nextDailyRun,
  ScheduledTask,
  ScheduledTaskAccess
} from "./scheduled-tasks";
import type { ThemeMode } from "./theme";
import { agentPortFromUrl } from "./url";

declare const require: ((id: string) => unknown) | undefined;

export type SandboxMode = "read-only" | "workspace-write" | "danger-full-access";
export type ApprovalPolicy = "never" | "on-request";
export type WebProviderName = "tavily" | "exa" | "talordata";

export interface WebApiKeys {
  tavily: string;
  exa: string;
  talordata: string;
}

export interface AgentSettings {
  agentUrl: string;
  apiBaseUrl: string;
  model: string;
  workspace: string;
  sandboxMode: SandboxMode;
  approvalPolicy: ApprovalPolicy;
  shellEnabled: boolean;
  disabledSkills: string[];
  sessionDbPath: string;
  themeMode: ThemeMode;
  configured: boolean;
  scheduledTasks: ScheduledTask[];
}

export interface AgentSkill {
  name: string;
  description: string;
  enabled: boolean;
}

export const DEFAULT_SETTINGS: AgentSettings = {
  agentUrl: "http://127.0.0.1:8000",
  apiBaseUrl: "https://api.openai.com/v1",
  model: "gpt-4.1-mini",
  workspace: "",
  sandboxMode: "workspace-write",
  approvalPolicy: "on-request",
  shellEnabled: false,
  disabledSkills: [],
  sessionDbPath: "",
  themeMode: "system",
  configured: false,
  scheduledTasks: []
};

export class AgentSettingTab extends PluginSettingTab {
  constructor(app: App, private readonly agentPlugin: CodeXAgentPlugin) {
    super(app, agentPlugin);
  }

  display(): void {
    this.containerEl.empty();
    this.containerEl.addClass("pka-settings-page");
    const next = {
      ...this.agentPlugin.settings,
      scheduledTasks: this.agentPlugin.settings.scheduledTasks.map((task) => ({
        ...task,
        permissions: { ...task.permissions },
        ...(task.lastRun ? { lastRun: { ...task.lastRun } } : {})
      }))
    };
    let apiKey = this.agentPlugin.getApiKey();
    let webApiKeys = { ...this.agentPlugin.getWebApiKeys() };
    const agentPort = agentPortFromUrl(next.agentUrl);
    next.agentUrl = `http://127.0.0.1:${agentPort}`;
    const originalSettings = JSON.stringify(next);
    const originalApiKey = apiKey;
    const originalWebApiKeys = JSON.stringify(webApiKeys);
    const needsInitialSave = !next.configured;
    let modelDropdown: DropdownComponent | null = null;
    let workspaceText: TextComponent | null = null;
    let sessionDbText: TextComponent | null = null;
    let actionLabel: HTMLElement | null = null;
    let discardButton: ButtonComponent | null = null;
    let saveButton: ButtonComponent | null = null;

    const updateDirtyState = () => {
      const changed = JSON.stringify(next) !== originalSettings
        || apiKey !== originalApiKey
        || JSON.stringify(webApiKeys) !== originalWebApiKeys;
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

    const webGroup = new SettingGroup(this.containerEl).setHeading("互联网搜索").addClass("pka-settings-group");
    addWebKeyInputs(
      webGroup,
      "Tavily API Keys",
      "保存到 SecretStorage，不写入 data.json；用于搜索和正文回退，多 Key 自动轮转。",
      webApiKeys.tavily,
      (value) => {
        webApiKeys = { ...webApiKeys, tavily: value };
        updateDirtyState();
      }
    );
    addWebKeyInputs(
      webGroup,
      "Exa API Keys",
      "保存到 SecretStorage，不写入 data.json；用于搜索和正文回退，多 Key 自动轮转。",
      webApiKeys.exa,
      (value) => {
        webApiKeys = { ...webApiKeys, exa: value };
        updateDirtyState();
      }
    );
    addWebKeyInputs(
      webGroup,
      "TalorData API Keys",
      "保存到 SecretStorage，不写入 data.json；仅参与搜索，多 Key 自动轮转。",
      webApiKeys.talordata,
      (value) => {
        webApiKeys = { ...webApiKeys, talordata: value };
        updateDirtyState();
      }
    );

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
      .setDesc("查看和修改 Agent 跨会话使用的长期记忆。")
      .addButton((button) => button.setButtonText("编辑").onClick(async () => {
        button.setDisabled(true).setButtonText("读取中…");
        try {
          const modal = new Modal(this.app).setTitle("编辑长期记忆");
          const editor = modal.contentEl.createEl("textarea", {
            cls: "pka-memory-editor",
            attr: { "aria-label": "长期记忆内容", placeholder: "暂无长期记忆。" }
          });
          editor.value = await this.agentPlugin.getLongTermMemory();
          modal.contentEl.createEl("p", {
            cls: "pka-memory-help",
            text: "保存后由你维护这份内容，后续自动整理不会覆盖。"
          });
          const actions = modal.contentEl.createDiv({ cls: "pka-memory-actions" });
          actions.createEl("button", { text: "取消" }).addEventListener("click", () => modal.close());
          const saveButton = actions.createEl("button", { cls: "mod-cta", text: "保存" });
          saveButton.addEventListener("click", async () => {
            saveButton.disabled = true;
            saveButton.textContent = "保存中…";
            try {
              await this.agentPlugin.updateLongTermMemory(editor.value);
              new Notice("长期记忆已保存。");
              modal.close();
            } catch (error) {
              new Notice(error instanceof Error ? error.message : "无法保存长期记忆。");
              saveButton.disabled = false;
              saveButton.textContent = "保存";
            }
          });
          modal.open();
          editor.focus();
        } catch (error) {
          new Notice(error instanceof Error ? error.message : "无法读取长期记忆。");
        } finally {
          button.setDisabled(false).setButtonText("编辑");
        }
      })));

    const scheduledGroup = new SettingGroup(this.containerEl)
      .setHeading("定时任务")
      .addClass("pka-settings-group", "pka-scheduled-tasks-group");
    const scheduledToolbar = new Setting(scheduledGroup.listEl)
      .setName("每日任务")
      .setDesc("Obsidian 打开期间每分钟检查一次；写入、联网权限按任务单独限制。")
      .addButton((button) => button.setButtonText("添加任务").onClick(() => {
        new ScheduledTaskModal(this.app, null, (task) => {
          next.scheduledTasks = [...next.scheduledTasks, task];
          renderScheduledTasks();
          updateDirtyState();
        }).open();
      }));
    scheduledToolbar.settingEl.addClass("pka-scheduled-task-toolbar");
    const scheduledList = scheduledGroup.listEl.createDiv({ cls: "pka-scheduled-task-list" });
    const renderScheduledTasks = () => {
      scheduledList.empty();
      if (!next.scheduledTasks.length) {
        renderSkillState(scheduledList, "还没有定时任务", "添加一条每日执行的 Agent 指令。");
        return;
      }
      for (const task of next.scheduledTasks) {
        const persisted = this.agentPlugin.settings.scheduledTasks.find(({ id }) => id === task.id);
        const runnable = persisted !== undefined && JSON.stringify(persisted) === JSON.stringify(task);
        const item = new Setting(scheduledList)
          .setName(task.name)
          .setDesc(scheduledTaskDescription(task))
          .addToggle((toggle) => toggle.setValue(task.enabled).onChange((enabled) => {
            task.enabled = enabled;
            if (enabled) task.nextRunAt = nextDailyRun(task.time);
            renderScheduledTasks();
            updateDirtyState();
          }))
          .addButton((button) => button
            .setButtonText("立即运行")
            .setDisabled(!runnable)
            .setTooltip(runnable ? "立即执行，不改变下次计划时间" : "请先保存任务")
            .onClick(async () => {
              button.setDisabled(true).setButtonText("运行中…");
              try {
                await this.agentPlugin.runScheduledTask(task.id);
                new Notice(`定时任务“${task.name}”已完成。`);
              } catch (error) {
                new Notice(error instanceof Error ? error.message : "定时任务执行失败。");
              } finally {
                this.display();
              }
            }))
          .addButton((button) => button.setButtonText("编辑").onClick(() => {
            new ScheduledTaskModal(this.app, task, (updated) => {
              next.scheduledTasks = next.scheduledTasks.map((candidate) =>
                candidate.id === updated.id ? updated : candidate
              );
              renderScheduledTasks();
              updateDirtyState();
            }).open();
          }))
          .addButton((button) => button.setWarning().setButtonText("删除").onClick(() => {
            next.scheduledTasks = next.scheduledTasks.filter(({ id }) => id !== task.id);
            renderScheduledTasks();
            updateDirtyState();
          }));
        item.settingEl.classList.toggle("is-disabled", !task.enabled);
      }
    };
    renderScheduledTasks();

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

    const skillGroup = new SettingGroup(this.containerEl)
      .setHeading("技能（Skills）")
      .addClass("pka-settings-group", "pka-skills-group");
    const folderInput = this.containerEl.createEl("input", { type: "file" });
    folderInput.multiple = true;
    folderInput.hidden = true;
    folderInput.setAttribute("webkitdirectory", "");
    const importSetting = new Setting(skillGroup.listEl)
      .setName("管理 Skills")
      .setDesc("关闭后文件仍会保留；保存并应用后生效。")
      .addButton((button) => button.setButtonText("导入 Skill").onClick(() => folderInput.click()));
    importSetting.settingEl.addClass("pka-skill-toolbar");
    const skillList = skillGroup.listEl.createDiv({ cls: "pka-skill-list" });
    folderInput.addEventListener("change", async () => {
      const files = Array.from(folderInput.files ?? []);
      folderInput.value = "";
      if (!files.length) return;
      importSetting.setDisabled(true);
      try {
        const skill = await this.agentPlugin.importSkill(files);
        new Notice(`已导入 Skill：${skill.name}`);
        await renderSkills();
      } catch (error) {
        new Notice(error instanceof Error ? error.message : "无法导入 Skill。");
      } finally {
        importSetting.setDisabled(false);
      }
    });
    let skillsLoaded = false;
    const renderSkills = async () => {
      await this.renderSkills(
        skillList,
        importSetting,
        skillsLoaded ? next.disabledSkills : null,
        (names) => {
          next.disabledSkills = names;
          updateDirtyState();
        }
      );
      skillsLoaded = true;
    };
    void renderSkills();

    const actionBar = this.containerEl.createDiv({ cls: "pka-settings-actions" });
    new Setting(actionBar)
      .setName("配置未修改")
      .addButton((button) => {
        discardButton = button.setButtonText("放弃更改").onClick(() => this.display());
      })
      .addButton((button) => {
        saveButton = button.setCta().setButtonText("保存并应用").onClick(async () => {
          if (await this.agentPlugin.applySettings(next, apiKey, webApiKeys)) this.display();
        });
      });
    actionLabel = actionBar.querySelector(".setting-item-name");
    updateDirtyState();
  }

  private async renderSkills(
    container: HTMLElement,
    toolbar: Setting,
    currentDisabled: readonly string[] | null,
    onDisabledChange: (names: string[]) => void
  ): Promise<void> {
    container.empty();
    renderSkillState(container, "正在读取 Skills…", "正在检查已导入的工作流。");
    try {
      const skills = await this.agentPlugin.listSkills();
      container.empty();
      if (!skills.length) {
        toolbar.setName("Skills").setDesc("导入包含 SKILL.md 的文件夹以添加工作流。");
        renderSkillState(container, "还没有 Skill", "点击上方“导入 Skill”添加可复用工作流。");
        return;
      }
      const disabled = new Set(
        currentDisabled ?? skills.filter((skill) => !skill.enabled).map((skill) => skill.name)
      );
      const updateSummary = () => toolbar
        .setName(`${skills.length} 个 Skill`)
        .setDesc(`${skills.length - disabled.size} 个已启用；关闭后文件仍会保留。`);
      updateSummary();
      onDisabledChange(Array.from(disabled).sort());
      for (const skill of skills) {
        const item = new Setting(container)
          .setName(skill.name)
          .setDesc(skill.description)
          .addToggle((toggle) => toggle
            .setValue(!disabled.has(skill.name))
            .onChange((enabled) => {
              if (enabled) disabled.delete(skill.name);
              else disabled.add(skill.name);
              item.settingEl.classList.toggle("is-disabled", !enabled);
              updateSummary();
              onDisabledChange(Array.from(disabled).sort());
            }));
        item.settingEl.addClass("pka-skill-item");
        item.settingEl.classList.toggle("is-disabled", disabled.has(skill.name));
      }
    } catch (error) {
      container.empty();
      toolbar.setName("Skills").setDesc("暂时无法读取已导入的工作流。");
      renderSkillState(
        container,
        "无法读取 Skills",
        error instanceof Error ? error.message : "Agent 暂时不可用。"
      );
    }
  }
}

class ScheduledTaskModal extends Modal {
  constructor(
    app: App,
    private readonly existing: ScheduledTask | null,
    private readonly save: (task: ScheduledTask) => void
  ) {
    super(app);
  }

  onOpen(): void {
    this.setTitle(this.existing ? "编辑定时任务" : "添加定时任务");
    let name = this.existing?.name ?? "";
    let time = this.existing?.time ?? "09:00";
    let instruction = this.existing?.instruction ?? "";
    let access: ScheduledTaskAccess = this.existing?.permissions.access ?? "workspace-write";
    let allowWeb = this.existing?.permissions.allowWeb ?? false;

    new Setting(this.contentEl).setName("名称").addText((text) => text
      .setPlaceholder("每日文档整理")
      .setValue(name)
      .onChange((value) => { name = value; }));
    new Setting(this.contentEl).setName("每日执行时间").addText((text) => {
      text.inputEl.type = "time";
      text.setValue(time).onChange((value) => { time = value; });
    });
    new Setting(this.contentEl).setName("任务指令").addTextArea((area) => {
      area.inputEl.rows = 8;
      area.inputEl.addClass("pka-scheduled-task-instruction");
      area.setPlaceholder("说明需要检查、修改和输出什么。")
        .setValue(instruction)
        .onChange((value) => { instruction = value; });
    });
    new Setting(this.contentEl).setName("工作区权限").addDropdown((dropdown) => dropdown
      .addOptions({ "read-only": "只读", "workspace-write": "允许修改 Vault" })
      .setValue(access)
      .onChange((value) => { access = value as ScheduledTaskAccess; }));
    new Setting(this.contentEl)
      .setName("允许访问互联网")
      .setDesc("关闭时不会向定时任务提供 Web 搜索和网页读取工具。")
      .addToggle((toggle) => toggle.setValue(allowWeb).onChange((value) => { allowWeb = value; }));

    const actions = this.contentEl.createDiv({ cls: "pka-memory-actions" });
    actions.createEl("button", { text: "取消" }).addEventListener("click", () => this.close());
    const saveButton = actions.createEl("button", { cls: "mod-cta", text: "保存任务" });
    saveButton.addEventListener("click", () => {
      try {
        const trimmedName = name.trim();
        const trimmedInstruction = instruction.trim();
        if (!trimmedName || !trimmedInstruction) throw new Error("任务名称和指令不能为空。");
        const scheduleChanged = !this.existing || this.existing.time !== time;
        this.save({
          ...(this.existing ?? {}),
          id: this.existing?.id ?? crypto.randomUUID(),
          name: trimmedName,
          enabled: this.existing?.enabled ?? true,
          time,
          instruction: trimmedInstruction,
          permissions: { access, allowWeb },
          nextRunAt: scheduleChanged ? nextDailyRun(time) : this.existing.nextRunAt
        });
        this.close();
      } catch (error) {
        new Notice(error instanceof Error ? error.message : "无法保存定时任务。");
      }
    });
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

function scheduledTaskDescription(task: ScheduledTask): string {
  const permission = task.permissions.access === "workspace-write" ? "可修改 Vault" : "只读";
  const next = task.enabled ? `下次：${new Date(task.nextRunAt).toLocaleString()}` : "已停用";
  const last = task.lastRun
    ? `上次：${task.lastRun.status === "success" ? "成功" : task.lastRun.status === "failed" ? "失败" : "运行中"}`
    : "尚未运行";
  return `${task.time} · ${permission}${task.permissions.allowWeb ? " · 可联网" : ""} · ${next} · ${last}`;
}

function addWebKeyInputs(
  group: SettingGroup,
  name: string,
  description: string,
  value: string,
  onChange: (value: string) => void
): void {
  const keys = value.split(/[\s,;]+/).map((key) => key.trim()).filter(Boolean);
  if (!keys.length) keys.push("");
  let activeIndex = 0;
  group.addSetting((setting) => {
    setting.setName(name).setDesc(description);
    const container = setting.controlEl.createDiv({ cls: "pka-web-key-control" });
    const render = (focus = false) => {
      container.empty();
      const input = container.createEl("input", {
        type: "password",
        cls: "pka-web-key-input",
        attr: { "aria-label": `${name} ${activeIndex + 1}`, autocomplete: "off", placeholder: "API Key" }
      });
      input.value = keys[activeIndex];
      const addIconButton = (
        icon: string,
        label: string,
        disabled: boolean,
        onClick: () => void
      ) => {
        const button = container.createEl("button", {
          cls: "clickable-icon",
          attr: { type: "button", "aria-label": label, title: label }
        });
        setIcon(button, icon);
        button.disabled = disabled;
        button.addEventListener("click", onClick);
        return button;
      };
      let revealed = false;
      const reveal = addIconButton("eye", `显示当前 ${name}`, false, () => {
        revealed = !revealed;
        input.type = revealed ? "text" : "password";
        const label = `${revealed ? "隐藏" : "显示"}当前 ${name}`;
        reveal.setAttribute("aria-label", label);
        reveal.title = label;
        setIcon(reveal, revealed ? "eye-off" : "eye");
      });
      addIconButton("chevron-left", `上一个 ${name}`, activeIndex === 0, () => {
        activeIndex -= 1;
        render(true);
      });
      container.createSpan({
        cls: "pka-web-key-counter",
        text: `${activeIndex + 1} / ${keys.length}`
      });
      addIconButton("chevron-right", `下一个 ${name}`, activeIndex === keys.length - 1, () => {
        activeIndex += 1;
        render(true);
      });
      addIconButton("trash-2", `删除当前 ${name}`, keys.length === 1, () => {
        keys.splice(activeIndex, 1);
        activeIndex = Math.min(activeIndex, keys.length - 1);
        onChange(keys.join("\n"));
        render(true);
      });
      const add = addIconButton("plus", `添加 ${name}`, !input.value.trim(), () => {
        keys.push("");
        activeIndex = keys.length - 1;
        onChange(keys.join("\n"));
        render(true);
      });
      input.addEventListener("input", () => {
        keys[activeIndex] = input.value;
        add.disabled = !input.value.trim();
        onChange(keys.join("\n"));
      });
      if (focus) input.focus();
    };
    render();
  });
}

function renderSkillState(container: HTMLElement, title: string, detail: string): void {
  const state = container.createDiv({ cls: "pka-skill-state" });
  state.createEl("strong", { text: title });
  state.createEl("span", { text: detail });
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
