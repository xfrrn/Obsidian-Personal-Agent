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
var AgentSettingTab = class extends import_obsidian.PluginSettingTab {
  constructor(app, agentPlugin) {
    super(app, agentPlugin);
    this.agentPlugin = agentPlugin;
  }
  display() {
    this.containerEl.empty();
    const next = { ...this.agentPlugin.settings };
    let apiKey = this.agentPlugin.getApiKey();
    new import_obsidian.Setting(this.containerEl).setName("CodeX-Agent \u5730\u5740").setDesc("\u4EC5\u5141\u8BB8\u672C\u673A\u56DE\u73AF\u5730\u5740\u3002").addText((text) => text.setValue(next.agentUrl).onChange((value) => {
      next.agentUrl = value;
    }));
    new import_obsidian.Setting(this.containerEl).setName("\u6A21\u578B API \u5730\u5740").addText((text) => text.setValue(next.apiBaseUrl).onChange((value) => {
      next.apiBaseUrl = value;
    }));
    new import_obsidian.Setting(this.containerEl).setName("\u6A21\u578B").addText((text) => text.setValue(next.model).onChange((value) => {
      next.model = value;
    }));
    new import_obsidian.Setting(this.containerEl).setName("API Key").setDesc("\u4FDD\u5B58\u5230 Obsidian SecretStorage\uFF0C\u4E0D\u5199\u5165 data.json\u3002").addText((text) => {
      text.inputEl.type = "password";
      text.setValue(apiKey).onChange((value) => {
        apiKey = value;
      });
    });
    new import_obsidian.Setting(this.containerEl).setName("Agent \u5DE5\u4F5C\u533A").setDesc("\u901A\u5E38\u586B\u5199\u5F53\u524D Vault \u7684\u7EDD\u5BF9\u8DEF\u5F84\u3002").addText((text) => text.setValue(next.workspace).onChange((value) => {
      next.workspace = value;
    }));
    new import_obsidian.Setting(this.containerEl).setName("\u6C99\u76D2\u6A21\u5F0F").addDropdown((dropdown) => dropdown.addOptions({
      "read-only": "\u53EA\u8BFB",
      "workspace-write": "\u5DE5\u4F5C\u533A\u5199\u5165",
      "danger-full-access": "\u5B8C\u5168\u8BBF\u95EE"
    }).setValue(next.sandboxMode).onChange((value) => {
      next.sandboxMode = value;
    }));
    new import_obsidian.Setting(this.containerEl).setName("\u5BA1\u6279\u7B56\u7565").addDropdown((dropdown) => dropdown.addOptions({ "on-request": "\u6309\u9700\u5BA1\u6279", never: "\u4ECE\u4E0D\u8BE2\u95EE" }).setValue(next.approvalPolicy).onChange((value) => {
      next.approvalPolicy = value;
    }));
    new import_obsidian.Setting(this.containerEl).setName("\u542F\u7528 Shell \u5DE5\u5177").setDesc("\u542F\u7528 exec_command \u548C write_stdin\u3002").addToggle((toggle) => toggle.setValue(next.shellEnabled).onChange((value) => {
      next.shellEnabled = value;
    }));
    new import_obsidian.Setting(this.containerEl).setName("\u4F1A\u8BDD\u6570\u636E\u5E93").setDesc("\u7559\u7A7A\u65F6\u7EE7\u7EED\u4F7F\u7528 Agent \u5F53\u524D\u8DEF\u5F84\u3002").addText((text) => text.setValue(next.sessionDbPath).onChange((value) => {
      next.sessionDbPath = value;
    }));
    new import_obsidian.Setting(this.containerEl).setName("\u754C\u9762\u4E3B\u9898").setDesc("\u9ED8\u8BA4\u8DDF\u968F Obsidian\uFF0C\u4E5F\u53EF\u56FA\u5B9A\u4E3A\u4EAE\u8272\u6216\u6697\u8272\u3002").addDropdown((dropdown) => dropdown.addOptions({ system: "\u8DDF\u968F Obsidian", light: "\u4EAE\u8272", dark: "\u6697\u8272" }).setValue(next.themeMode).onChange((value) => {
      next.themeMode = value;
    }));
    new import_obsidian.Setting(this.containerEl).addButton((button) => button.setCta().setButtonText("\u4FDD\u5B58\u5E76\u5E94\u7528").onClick(async () => this.agentPlugin.applySettings(next, apiKey)));
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
function normalizeApiBaseUrl(value) {
  const url = new URL(value.trim());
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) {
    throw new Error("\u6A21\u578B\u5730\u5740\u5FC5\u987B\u662F\u65E0\u5185\u5D4C\u51ED\u636E\u7684 HTTP \u6216 HTTPS \u5730\u5740\u3002");
  }
  return url.toString().replace(/\/$/, "");
}

// apps/obsidian-plugin/src/codex-agent-view.ts
var import_obsidian2 = require("obsidian");

// apps/obsidian-plugin/src/bridge.ts
var PANEL_MESSAGE_SOURCE = "codex-agent-ui";
var HOST_MESSAGE_SOURCE = "obsidian-agent-plugin";
function parsePanelMessage(value) {
  if (!isRecord(value) || value.source !== PANEL_MESSAGE_SOURCE) return null;
  if (value.type === "ready") return { type: "ready" };
  if (value.type !== "settings:update" || !isRecord(value.settings)) return null;
  if (Object.keys(value.settings).some((key) => !["sandboxMode", "themeMode"].includes(key))) return null;
  const settings = {};
  if (value.settings.sandboxMode !== void 0) {
    if (!isSandboxMode(value.settings.sandboxMode)) return null;
    settings.sandboxMode = value.settings.sandboxMode;
  }
  if (value.settings.themeMode !== void 0) {
    if (!isThemeMode(value.settings.themeMode)) return null;
    settings.themeMode = value.settings.themeMode;
  }
  return Object.keys(settings).length ? { type: "settings:update", settings } : null;
}
function isThemeMode(value) {
  return value === "system" || value === "light" || value === "dark";
}
function isSandboxMode(value) {
  return value === "read-only" || value === "workspace-write" || value === "danger-full-access";
}
function isRecord(value) {
  return typeof value === "object" && value !== null;
}

// apps/obsidian-plugin/src/codex-agent-view.ts
var CODEX_AGENT_VIEW_TYPE = "codex-agent-view";
var CodeXAgentView = class extends import_obsidian2.ItemView {
  constructor(leaf, getAgentUrl, syncAgentSettings, getPanelSettings, persistPanelSettings) {
    super(leaf);
    this.getAgentUrl = getAgentUrl;
    this.syncAgentSettings = syncAgentSettings;
    this.getPanelSettings = getPanelSettings;
    this.persistPanelSettings = persistPanelSettings;
    this.frame = null;
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
    this.registerDomEvent(window, "message", (event) => void this.handleMessage(event));
    this.registerEvent(this.app.workspace.on("css-change", () => this.postHostState()));
    this.registerEvent(this.app.workspace.on("file-open", () => this.postHostState()));
    await this.refresh();
  }
  async refresh() {
    const content = this.containerEl.children[1];
    content.empty();
    content.addClass("codex-agent-view");
    try {
      const agentUrl = normalizeAgentUrl(this.getAgentUrl());
      await this.syncAgentSettings();
      const response = await (0, import_obsidian2.requestUrl)({ url: `${agentUrl}/api/config`, method: "GET", throw: false });
      if (response.status < 200 || response.status >= 300) throw new Error(`HTTP ${response.status}`);
      const frame = content.createEl("iframe", { cls: "codex-agent-frame" });
      this.frame = frame;
      frame.src = agentUrl;
      frame.title = "CodeX-Agent";
      frame.allow = "clipboard-read; clipboard-write";
      frame.addEventListener("load", () => this.postHostState(), { once: true });
    } catch (error) {
      this.frame = null;
      const disconnected = content.createDiv({ cls: "codex-agent-disconnected" });
      disconnected.createEl("strong", { text: "\u65E0\u6CD5\u8FDE\u63A5\u672C\u5730 Agent" });
      disconnected.createEl("span", { text: error instanceof Error ? error.message : "\u8BF7\u786E\u8BA4\u670D\u52A1\u5DF2\u7ECF\u542F\u52A8\u3002" });
      disconnected.createEl("button", { text: "\u91CD\u65B0\u8FDE\u63A5" }).addEventListener("click", () => void this.refresh());
    }
  }
  async handleMessage(event) {
    var _a;
    if (!((_a = this.frame) == null ? void 0 : _a.contentWindow) || event.source !== this.frame.contentWindow) return;
    if (event.origin !== new URL(normalizeAgentUrl(this.getAgentUrl())).origin) return;
    const message = parsePanelMessage(event.data);
    if (!message) return;
    if (message.type === "settings:update") await this.persistPanelSettings(message.settings);
    this.postHostState();
  }
  postHostState() {
    var _a, _b, _c, _d, _e;
    const target = (_a = this.frame) == null ? void 0 : _a.contentWindow;
    if (!target) return;
    const style = getComputedStyle(document.body);
    const token = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
    const adapter = this.app.vault.adapter;
    const settings = this.getPanelSettings();
    target.postMessage({
      source: HOST_MESSAGE_SOURCE,
      type: "host:state",
      theme: {
        mode: settings.themeMode,
        isDark: document.body.classList.contains("theme-dark"),
        tokens: {
          backgroundPrimary: token("--background-primary", "#ffffff"),
          backgroundSecondary: token("--background-secondary", "#f6f6f6"),
          backgroundHover: token("--background-modifier-hover", "rgba(0, 0, 0, 0.075)"),
          border: token("--background-modifier-border", "#dddddd"),
          textNormal: token("--text-normal", "#222222"),
          textMuted: token("--text-muted", "#666666"),
          textAccent: token("--text-accent", "#7f6df2"),
          textOnAccent: token("--text-on-accent", "#ffffff"),
          textError: token("--text-error", "#d14b4b"),
          textSuccess: token("--text-success", "#2d9d5b"),
          fontInterface: token("--font-interface-theme", "system-ui"),
          fontText: token("--font-text-theme", "system-ui")
        }
      },
      context: {
        workspace: (_c = (_b = adapter.getBasePath) == null ? void 0 : _b.call(adapter)) != null ? _c : "",
        activeFile: (_e = (_d = this.app.workspace.getActiveFile()) == null ? void 0 : _d.path) != null ? _e : null
      },
      settings: { sandboxMode: settings.sandboxMode }
    }, new URL(normalizeAgentUrl(this.getAgentUrl())).origin);
  }
};

// apps/obsidian-plugin/src/agent-process.ts
function agentLaunchSpec(agentUrl, executable = "") {
  const url = new URL(agentUrl);
  if (url.protocol !== "http:") {
    throw new Error("\u81EA\u52A8\u542F\u52A8\u672C\u5730 Agent \u53EA\u652F\u6301 HTTP \u5730\u5740\u3002");
  }
  if (url.hostname === "[::1]") {
    throw new Error("\u81EA\u52A8\u542F\u52A8\u672C\u5730 Agent \u6682\u4E0D\u652F\u6301 IPv6 \u5730\u5740\u3002");
  }
  const host = url.hostname === "localhost" ? "127.0.0.1" : url.hostname;
  return {
    command: executable || "python",
    args: [
      ...executable ? [] : ["-m", "agent.web.server"],
      "--host",
      host,
      "--port",
      url.port || "80"
    ]
  };
}

// apps/obsidian-plugin/src/main.ts
var API_KEY_SECRET_ID = "personal-knowledge-agent-api-key";
var AGENT_START_ATTEMPTS = 100;
var { existsSync } = require("node:fs");
var { spawn } = require("node:child_process");
var CodeXAgentPlugin = class extends import_obsidian3.Plugin {
  constructor() {
    super(...arguments);
    this.apiKey = "";
    this.agentProcess = null;
    this.agentStartPromise = null;
    this.agentStartError = "";
    this.unloading = false;
  }
  async onload() {
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
    this.addRibbonIcon("bot", "\u6253\u5F00 CodeX Agent", () => void this.activateView());
    this.addCommand({
      id: "open-codex-agent",
      name: "\u6253\u5F00 CodeX Agent",
      callback: () => void this.activateView()
    });
    this.addSettingTab(new AgentSettingTab(this.app, this));
    void this.syncAgentSettings().catch((error) => {
      if (!this.unloading) {
        new import_obsidian3.Notice(error instanceof Error ? error.message : "\u65E0\u6CD5\u81EA\u52A8\u542F\u52A8\u672C\u5730 Agent\u3002");
      }
    });
  }
  onunload() {
    this.unloading = true;
    this.stopLocalAgent();
    this.app.workspace.detachLeavesOfType(CODEX_AGENT_VIEW_TYPE);
  }
  getApiKey() {
    return this.apiKey;
  }
  async persistPanelSettings(settings) {
    this.settings = { ...this.settings, ...settings };
    await this.saveData(this.settings);
  }
  async applySettings(value, apiKey) {
    const previousAgentUrl = this.settings.agentUrl;
    try {
      const workspace = value.workspace.trim();
      const model = value.model.trim();
      if (!workspace || !model) throw new Error("\u6A21\u578B\u548C Agent \u5DE5\u4F5C\u533A\u4E0D\u80FD\u4E3A\u7A7A\u3002");
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
      if (this.settings.agentUrl !== previousAgentUrl) this.stopLocalAgent();
    } catch (error) {
      new import_obsidian3.Notice(error instanceof Error ? error.message : "\u65E0\u6CD5\u4FDD\u5B58 Agent \u914D\u7F6E\u3002");
      return;
    }
    try {
      await this.syncAgentSettings(true);
      for (const leaf of this.app.workspace.getLeavesOfType(CODEX_AGENT_VIEW_TYPE)) {
        if (leaf.view instanceof CodeXAgentView) void leaf.view.refresh();
      }
      new import_obsidian3.Notice("Agent \u914D\u7F6E\u5DF2\u4FDD\u5B58\u5E76\u5E94\u7528\u3002");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Agent \u6682\u65F6\u4E0D\u53EF\u7528\u3002";
      new import_obsidian3.Notice(`\u914D\u7F6E\u5DF2\u4FDD\u5B58\uFF0C\u4F46\u5C1A\u672A\u5E94\u7528\uFF1A${message}`);
    }
  }
  async syncAgentSettings(includeEmptyApiKey = false) {
    await this.ensureAgentStarted();
    const body = this.settings.configured ? {
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
    const response = await (0, import_obsidian3.requestUrl)({
      url: `${this.settings.agentUrl}/api/config`,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      throw: false
    });
    if (response.status < 200 || response.status >= 300) {
      const payload = response.json;
      throw new Error(typeof (payload == null ? void 0 : payload.error) === "string" ? payload.error : `Agent \u914D\u7F6E\u5931\u8D25\uFF1AHTTP ${response.status}`);
    }
  }
  async activateView() {
    await this.syncAgentSettings().catch(() => void 0);
    const existing = this.app.workspace.getLeavesOfType(CODEX_AGENT_VIEW_TYPE)[0];
    const leaf = existing != null ? existing : this.app.workspace.getRightLeaf(false);
    if (!leaf) return;
    if (!existing) await leaf.setViewState({ type: CODEX_AGENT_VIEW_TYPE, active: true });
    await this.app.workspace.revealLeaf(leaf);
  }
  async loadSettings() {
    var _a, _b, _c;
    const saved = await this.loadData();
    const adapter = this.app.vault.adapter;
    const configured = (saved == null ? void 0 : saved.configured) === true;
    let agentUrl = DEFAULT_SETTINGS.agentUrl;
    let apiBaseUrl = DEFAULT_SETTINGS.apiBaseUrl;
    try {
      agentUrl = normalizeAgentUrl(typeof (saved == null ? void 0 : saved.agentUrl) === "string" ? saved.agentUrl : agentUrl);
      apiBaseUrl = normalizeApiBaseUrl(typeof (saved == null ? void 0 : saved.apiBaseUrl) === "string" ? saved.apiBaseUrl : apiBaseUrl);
    } catch (e) {
    }
    this.settings = {
      agentUrl,
      apiBaseUrl,
      model: typeof (saved == null ? void 0 : saved.model) === "string" ? saved.model : DEFAULT_SETTINGS.model,
      workspace: configured && typeof (saved == null ? void 0 : saved.workspace) === "string" && saved.workspace.trim() ? saved.workspace : (_b = (_a = adapter.getBasePath) == null ? void 0 : _a.call(adapter)) != null ? _b : "",
      sandboxMode: isSandboxMode2(saved == null ? void 0 : saved.sandboxMode) ? saved.sandboxMode : DEFAULT_SETTINGS.sandboxMode,
      approvalPolicy: isApprovalPolicy(saved == null ? void 0 : saved.approvalPolicy) ? saved.approvalPolicy : DEFAULT_SETTINGS.approvalPolicy,
      shellEnabled: typeof (saved == null ? void 0 : saved.shellEnabled) === "boolean" ? saved.shellEnabled : DEFAULT_SETTINGS.shellEnabled,
      sessionDbPath: typeof (saved == null ? void 0 : saved.sessionDbPath) === "string" ? saved.sessionDbPath : DEFAULT_SETTINGS.sessionDbPath,
      themeMode: isThemeMode(saved == null ? void 0 : saved.themeMode) ? saved.themeMode : DEFAULT_SETTINGS.themeMode,
      configured
    };
    this.apiKey = (_c = this.app.secretStorage.getSecret(API_KEY_SECRET_ID)) != null ? _c : "";
  }
  /** 已有服务直接复用；连接失败时只启动一个由插件托管的 Python 子进程。 */
  async ensureAgentStarted() {
    var _a;
    if (await this.isAgentAvailable()) return;
    (_a = this.agentStartPromise) != null ? _a : this.agentStartPromise = this.startLocalAgent().finally(() => {
      this.agentStartPromise = null;
    });
    await this.agentStartPromise;
  }
  async isAgentAvailable() {
    try {
      const response = await (0, import_obsidian3.requestUrl)({
        url: `${this.settings.agentUrl}/api/config`,
        method: "GET",
        throw: false
      });
      return response.status >= 200 && response.status < 300;
    } catch (e) {
      return false;
    }
  }
  /** 启动 Agent 并等待 HTTP 入口就绪，避免侧栏先显示一次断线页。 */
  async startLocalAgent() {
    var _a;
    if (!this.agentProcess) {
      const spec = agentLaunchSpec(this.settings.agentUrl, this.bundledAgentPath());
      this.agentStartError = "";
      const child = spawn(spec.command, spec.args, {
        windowsHide: true,
        stdio: ["ignore", "ignore", "pipe"]
      });
      this.agentProcess = child;
      (_a = child.stderr) == null ? void 0 : _a.on("data", (chunk) => {
        this.agentStartError = `${this.agentStartError}${chunk.toString()}`.trim().slice(-1e3);
      });
      child.once("error", (error) => {
        this.agentStartError = error.message;
        if (this.agentProcess === child) this.agentProcess = null;
      });
      child.once("exit", (code) => {
        if (!this.agentStartError) this.agentStartError = `Python \u8FDB\u7A0B\u5DF2\u9000\u51FA\uFF08\u4EE3\u7801 ${code != null ? code : "\u672A\u77E5"}\uFF09`;
        if (this.agentProcess === child) this.agentProcess = null;
      });
    }
    for (let attempt = 0; attempt < AGENT_START_ATTEMPTS; attempt += 1) {
      if (await this.isAgentAvailable()) return;
      if (!this.agentProcess) break;
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    this.stopLocalAgent();
    const detail = this.agentStartError ? `\uFF1A${this.agentStartError}` : "";
    throw new Error(`\u65E0\u6CD5\u81EA\u52A8\u542F\u52A8\u672C\u5730 Agent\uFF0C\u8BF7\u68C0\u67E5 Windows \u4E00\u4F53\u5305\u662F\u5426\u5B8C\u6574${detail}`);
  }
  /** 返回插件包内 Agent EXE；源码开发时不存在则回退到系统 Python。 */
  bundledAgentPath() {
    const adapter = this.app.vault.adapter;
    if (!this.manifest.dir || !adapter.getFullPath) return "";
    const path = adapter.getFullPath(`${this.manifest.dir}/agent/codex-agent.exe`);
    return existsSync(path) ? path : "";
  }
  /** 只终止本插件创建的子进程，不影响用户手动运行的 Agent。 */
  stopLocalAgent() {
    var _a;
    (_a = this.agentProcess) == null ? void 0 : _a.kill();
    this.agentProcess = null;
  }
};
function isSandboxMode2(value) {
  return value === "read-only" || value === "workspace-write" || value === "danger-full-access";
}
function isApprovalPolicy(value) {
  return value === "never" || value === "on-request";
}
