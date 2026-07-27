"use strict";
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// apps/obsidian-plugin/src/main.ts
var main_exports = {};
__export(main_exports, {
  default: () => CodeXAgentPlugin
});
module.exports = __toCommonJS(main_exports);
var import_obsidian3 = require("obsidian");

// apps/obsidian-plugin/src/settings.ts
var import_obsidian = require("obsidian");
var DEFAULT_SETTINGS = {
  agentUrl: "http://127.0.0.1:8000"
};
var AgentSettingTab = class extends import_obsidian.PluginSettingTab {
  constructor(app, agentPlugin) {
    super(app, agentPlugin);
    this.agentPlugin = agentPlugin;
  }
  display() {
    this.containerEl.empty();
    let nextUrl = this.agentPlugin.settings.agentUrl;
    new import_obsidian.Setting(this.containerEl).setName("CodeX-Agent \u5730\u5740").setDesc("\u4EC5\u5141\u8BB8\u672C\u673A\u56DE\u73AF\u5730\u5740\u3002\u6A21\u578B\u3001\u5BC6\u94A5\u3001\u5DE5\u4F5C\u533A\u3001\u6C99\u7BB1\u548C\u5DE5\u5177\u5747\u5728 CodeX-Agent \u7684 .env \u4E2D\u914D\u7F6E\u3002").addText((text) => text.setPlaceholder(DEFAULT_SETTINGS.agentUrl).setValue(nextUrl).onChange((value) => {
      nextUrl = value;
    })).addButton((button) => button.setButtonText("\u5E94\u7528").onClick(async () => this.agentPlugin.setAgentUrl(nextUrl)));
  }
};

// apps/obsidian-plugin/src/url.ts
var LOOPBACK_HOSTS = /* @__PURE__ */ new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);
function normalizeAgentUrl(value) {
  const url = new URL(value.trim());
  if (!["http:", "https:"].includes(url.protocol)) {
    throw new Error("CodeX-Agent \u5730\u5740\u5FC5\u987B\u4F7F\u7528 HTTP \u6216 HTTPS\u3002");
  }
  if (url.username || url.password || !LOOPBACK_HOSTS.has(url.hostname.toLowerCase())) {
    throw new Error("CodeX-Agent \u5730\u5740\u5FC5\u987B\u6307\u5411\u672C\u673A\u56DE\u73AF\u5730\u5740\u3002");
  }
  return url.origin;
}

// apps/obsidian-plugin/src/codex-agent-view.ts
var import_obsidian2 = require("obsidian");
var CODEX_AGENT_VIEW_TYPE = "codex-agent-view";
var CodeXAgentView = class extends import_obsidian2.ItemView {
  constructor(leaf, getAgentUrl) {
    super(leaf);
    this.getAgentUrl = getAgentUrl;
  }
  getViewType() {
    return CODEX_AGENT_VIEW_TYPE;
  }
  getDisplayText() {
    return "CodeX Agent";
  }
  getIcon() {
    return "bot";
  }
  async onOpen() {
    this.refresh();
  }
  refresh() {
    const content = this.containerEl.children[1];
    content.empty();
    content.addClass("codex-agent-view");
    const toolbar = content.createDiv({ cls: "codex-agent-toolbar" });
    toolbar.createSpan({ text: "CodeX-Agent" });
    toolbar.createEl("button", { text: "\u91CD\u65B0\u52A0\u8F7D" }).addEventListener("click", () => this.refresh());
    try {
      const frame = content.createEl("iframe", { cls: "codex-agent-frame" });
      frame.src = normalizeAgentUrl(this.getAgentUrl());
      frame.title = "CodeX-Agent";
      frame.allow = "clipboard-read; clipboard-write";
    } catch (error) {
      content.createDiv({
        cls: "codex-agent-error",
        text: error instanceof Error ? error.message : "CodeX-Agent \u5730\u5740\u65E0\u6548\u3002"
      });
    }
  }
};

// apps/obsidian-plugin/src/main.ts
var CodeXAgentPlugin = class extends import_obsidian3.Plugin {
  async onload() {
    await this.loadSettings();
    this.registerView(
      CODEX_AGENT_VIEW_TYPE,
      (leaf) => new CodeXAgentView(leaf, () => this.settings.agentUrl)
    );
    this.addRibbonIcon("bot", "\u6253\u5F00 CodeX Agent", () => void this.activateView());
    this.addCommand({
      id: "open-codex-agent",
      name: "\u6253\u5F00 CodeX Agent",
      callback: () => void this.activateView()
    });
    this.addSettingTab(new AgentSettingTab(this.app, this));
  }
  onunload() {
    this.app.workspace.detachLeavesOfType(CODEX_AGENT_VIEW_TYPE);
  }
  async setAgentUrl(value) {
    try {
      this.settings.agentUrl = normalizeAgentUrl(value);
      await this.saveData(this.settings);
      for (const leaf of this.app.workspace.getLeavesOfType(CODEX_AGENT_VIEW_TYPE)) {
        if (leaf.view instanceof CodeXAgentView) leaf.view.refresh();
      }
    } catch (error) {
      new import_obsidian3.Notice(error instanceof Error ? error.message : "CodeX-Agent \u5730\u5740\u65E0\u6548\u3002");
    }
  }
  async activateView() {
    const existing = this.app.workspace.getLeavesOfType(CODEX_AGENT_VIEW_TYPE)[0];
    const leaf = existing != null ? existing : this.app.workspace.getRightLeaf(false);
    if (!leaf) return;
    if (!existing) {
      await leaf.setViewState({ type: CODEX_AGENT_VIEW_TYPE, active: true });
    }
    await this.app.workspace.revealLeaf(leaf);
  }
  async loadSettings() {
    const saved = await this.loadData();
    const value = typeof saved === "object" && saved !== null ? saved.agentUrl : void 0;
    try {
      this.settings = { agentUrl: normalizeAgentUrl(value != null ? value : DEFAULT_SETTINGS.agentUrl) };
    } catch (e) {
      this.settings = { ...DEFAULT_SETTINGS };
    }
  }
};
