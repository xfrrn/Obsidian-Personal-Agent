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
  default: () => PersonalKnowledgeAgentPlugin
});
module.exports = __toCommonJS(main_exports);
var import_obsidian5 = require("obsidian");

// apps/obsidian-plugin/src/views/assistant-view/assistant-view.ts
var import_obsidian3 = require("obsidian");

// apps/obsidian-plugin/src/api/local-agent-client.ts
var import_obsidian2 = require("obsidian");

// apps/obsidian-plugin/src/utils/protocol.ts
var AgentError = class extends Error {
  constructor(message) {
    super(message);
    this.name = "AgentError";
  }
};

// apps/obsidian-plugin/src/obsidian/file-references.ts
var import_obsidian = require("obsidian");
function extractFileReferencePaths(app, text) {
  const files = app.vault.getMarkdownFiles().sort((a, b) => a.path.localeCompare(b.path));
  const result = [];
  for (const label of fileReferenceLabels(text)) {
    const path = resolveFileReference(files, label);
    if (path && !result.includes(path)) result.push(path);
  }
  return result;
}
function fileReferenceLabels(text) {
  var _a, _b;
  const labels = [];
  const pattern = /(^|[\s([{])@(?:\[\[([^\]\n]+)\]\]|([^\s,.;:!?()[\]{}"'`<>，。；：！？（）【】《》]+))/g;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    labels.push(((_b = (_a = match[2]) != null ? _a : match[3]) != null ? _b : "").split("|", 1)[0].split("#", 1)[0].trim());
  }
  return labels.filter(Boolean);
}
function resolveFileReference(files, label) {
  const target = stripMd((0, import_obsidian.normalizePath)(label)).toLocaleLowerCase();
  const matches = files.filter((file) => {
    var _a;
    const name = (_a = file.path.split("/").pop()) != null ? _a : file.path;
    return [file.path, name].some((key) => stripMd(key).toLocaleLowerCase() === target);
  });
  return matches.length === 1 ? matches[0].path : void 0;
}
function stripMd(value) {
  return value.endsWith(".md") ? value.slice(0, -3) : value;
}

// apps/obsidian-plugin/src/api/local-agent-client.ts
async function listConversations(settings) {
  const payload = await localAgentRequest(settings, "/api/sessions");
  if (!isRecord(payload) || !Array.isArray(payload.sessions)) {
    throw new AgentError("\u672C\u5730 Agent \u8FD4\u56DE\u4E86\u65E0\u6CD5\u8BC6\u522B\u7684\u4F1A\u8BDD\u5217\u8868\u3002");
  }
  return payload.sessions.filter(isConversationSummary);
}
async function createConversation(settings) {
  const payload = await localAgentRequest(settings, "/api/sessions", {});
  if (!isConversationSummary(payload)) throw new AgentError("\u672C\u5730 Agent \u65E0\u6CD5\u521B\u5EFA\u4F1A\u8BDD\u3002");
  return payload;
}
async function loadConversation(settings, sessionId) {
  const payload = await localAgentRequest(settings, `/api/sessions/${encodeURIComponent(sessionId)}`);
  if (!isRecord(payload) || !isConversationSummary(payload.session) || !Array.isArray(payload.messages)) {
    throw new AgentError("\u672C\u5730 Agent \u8FD4\u56DE\u4E86\u65E0\u6CD5\u8BC6\u522B\u7684\u4F1A\u8BDD\u8BE6\u60C5\u3002");
  }
  return {
    session: payload.session,
    messages: payload.messages.filter(isConversationMessage),
    pendingOperationPlan: isRecord(payload.pendingOperationPlan) ? toOperationPlan(payload.pendingOperationPlan) : void 0
  };
}
async function archiveConversation(settings, sessionId) {
  await localAgentRequest(settings, `/api/sessions/${encodeURIComponent(sessionId)}/archive`, {});
}
async function streamConversation(app, settings, sessionId, text, mode, scope, onEvent) {
  var _a;
  const port = localAgentPort(settings);
  if (!port || !settings.localAgentToken) throw new AgentError("\u672C\u5730 Agent \u5C1A\u672A\u8FDE\u63A5\u3002");
  const activeFile = app.workspace.getActiveFile();
  const selectedText = (_a = app.workspace.getActiveViewOfType(import_obsidian2.MarkdownView)) == null ? void 0 : _a.editor.getSelection();
  const response = await fetch(`http://127.0.0.1:${port}/api/sessions/${encodeURIComponent(sessionId)}/messages/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Agent-Token": settings.localAgentToken
    },
    body: JSON.stringify({
      text,
      mode,
      context: {
        scope,
        activeFilePath: activeFile == null ? void 0 : activeFile.path,
        selectedText: selectedText || void 0,
        referencedPaths: extractFileReferencePaths(app, text)
      }
    })
  });
  if (!response.ok || !response.body) {
    throw new AgentError(await responseError(response, "\u672C\u5730 Agent \u6D41\u5F0F\u8BF7\u6C42\u5931\u8D25\u3002"));
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  while (true) {
    const { done, value } = await reader.read();
    pending += decoder.decode(value != null ? value : new Uint8Array(), { stream: !done });
    const parsed = parseSseFrames(pending, done);
    pending = parsed.pending;
    for (const event of parsed.events) onEvent(event);
    if (done) return;
  }
}
function parseSseFrames(source, flush = false) {
  var _a;
  const normalized = source.replace(/\r\n/g, "\n");
  const frames = normalized.split("\n\n");
  const pending = flush ? "" : (_a = frames.pop()) != null ? _a : "";
  const events = [];
  for (const frame of frames) {
    const data = frame.split("\n").find((line) => line.startsWith("data:"));
    if (!data) continue;
    const value = JSON.parse(data.slice(5).trim());
    if (isAgentEvent(value)) events.push(value);
  }
  if (flush && frames.length === 0 && normalized.trim()) {
    const data = normalized.split("\n").find((line) => line.startsWith("data:"));
    if (data) {
      const value = JSON.parse(data.slice(5).trim());
      if (isAgentEvent(value)) events.push(value);
    }
  }
  return { events, pending };
}
async function resolveApproval(settings, sessionId, callId, submissionId, approved) {
  await localAgentRequest(settings, `/api/sessions/${encodeURIComponent(sessionId)}/approvals`, {
    callId,
    submissionId,
    approved
  });
}
async function executeLocalOperationPlan(settings, plan) {
  var _a;
  if (!plan.planId) throw new AgentError("\u64CD\u4F5C\u8BA1\u5212\u7F3A\u5C11 ID\u3002");
  const payload = await localAgentRequest(
    settings,
    `/operations/${encodeURIComponent(plan.planId)}/execute`,
    { confirmationToken: plan.confirmationToken }
  );
  if (!isRecord(payload) || !Array.isArray(payload.results)) {
    throw new AgentError("\u672C\u5730 Agent \u8FD4\u56DE\u4E86\u65E0\u6CD5\u8BC6\u522B\u7684\u6267\u884C\u7ED3\u679C\u3002");
  }
  plan.rollbackToken = optionalString(payload.rollbackToken);
  plan.status = (_a = optionalString(payload.status)) != null ? _a : "succeeded";
  return payload.results.map(operationResultText);
}
async function rollbackLocalOperationPlan(settings, plan) {
  if (!plan.planId) throw new AgentError("\u64CD\u4F5C\u8BA1\u5212\u7F3A\u5C11 ID\u3002");
  const payload = await localAgentRequest(
    settings,
    `/operations/${encodeURIComponent(plan.planId)}/rollback`,
    { rollbackToken: plan.rollbackToken }
  );
  if (!isRecord(payload) || !Array.isArray(payload.results)) {
    throw new AgentError("\u672C\u5730 Agent \u8FD4\u56DE\u4E86\u65E0\u6CD5\u8BC6\u522B\u7684\u64A4\u9500\u7ED3\u679C\u3002");
  }
  plan.status = "rolled_back";
  return payload.results.map(() => `\u5DF2\u64A4\u9500\u64CD\u4F5C\u8BA1\u5212\uFF1A${plan.planId}`);
}
async function listLocalAgentTools(settings) {
  const payload = await localAgentRequest(settings, "/tools");
  if (!isRecord(payload) || !Array.isArray(payload.tools)) {
    throw new AgentError("\u672C\u5730 Agent \u8FD4\u56DE\u4E86\u65E0\u6CD5\u8BC6\u522B\u7684\u5DE5\u5177\u5217\u8868\u3002");
  }
  return payload.tools.filter(isLocalAgentTool);
}
async function updateLocalAgentPolicy(app, settings) {
  var _a;
  if (!settings.localAgentToken) return;
  await localAgentRequest(settings, "/policy", {
    executionMode: settings.executionMode,
    llm: {
      baseUrl: settings.apiBaseUrl,
      model: settings.model.trim(),
      apiKey: (_a = app.secretStorage.getSecret(settings.secretId)) != null ? _a : ""
    }
  });
}
async function testLocalAgent(app, settings) {
  const port = localAgentPort(settings);
  if (!port || !settings.localAgentToken) throw new AgentError("\u672C\u5730 Agent \u5C1A\u672A\u8FDE\u63A5\u3002");
  const response = await (0, import_obsidian2.requestUrl)({
    url: `http://127.0.0.1:${port}/identity`,
    method: "GET",
    headers: { "X-Agent-Token": settings.localAgentToken },
    throw: false
  });
  if (response.status < 200 || response.status >= 300) {
    throw new AgentError(`\u672C\u5730 Agent \u4E0D\u53EF\u7528\uFF08HTTP ${response.status}\uFF09\u3002`);
  }
  const payload = response.json;
  if (!isRecord(payload) || payload.vaultRoot !== vaultBasePath(app)) {
    throw new AgentError("\u672C\u5730 Agent \u7ED1\u5B9A\u7684\u4E0D\u662F\u5F53\u524D Vault\u3002");
  }
}
async function discoverLocalAgent(app, settings) {
  var _a;
  const vaultPath = vaultBasePath(app);
  const ports = candidatePorts(settings);
  if (settings.localAgentToken) {
    for (const port of ports) {
      try {
        const response = await withTimeout((0, import_obsidian2.requestUrl)({
          url: `http://127.0.0.1:${port}/identity`,
          method: "GET",
          headers: { "X-Agent-Token": settings.localAgentToken },
          throw: false
        }), 400);
        const payload = response.json;
        if (response.status >= 200 && response.status < 300 && isRecord(payload) && payload.vaultRoot === vaultPath) {
          return { port: String(port), token: settings.localAgentToken, vaultRoot: vaultPath };
        }
      } catch (e) {
      }
    }
  }
  for (const port of ports) {
    try {
      await withTimeout((0, import_obsidian2.requestUrl)({ url: `http://127.0.0.1:${port}/health`, method: "GET", throw: false }), 400);
      const response = await withTimeout((0, import_obsidian2.requestUrl)({
        url: `http://127.0.0.1:${port}/handshake`,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          vaultPath,
          executionMode: settings.executionMode,
          llm: {
            baseUrl: settings.apiBaseUrl,
            model: settings.model.trim(),
            apiKey: (_a = app.secretStorage.getSecret(settings.secretId)) != null ? _a : ""
          }
        }),
        throw: false
      }), 1500);
      const payload = response.json;
      if (response.status >= 200 && response.status < 300 && isRecord(payload) && typeof payload.token === "string") {
        return {
          port: String(port),
          token: payload.token,
          vaultRoot: typeof payload.vaultRoot === "string" ? payload.vaultRoot : vaultPath
        };
      }
    } catch (e) {
    }
  }
  throw new AgentError("\u6CA1\u6709\u53D1\u73B0\u53EF\u7528\u7684\u672C\u5730 Agent\u3002\u8BF7\u5148\u542F\u52A8 local-agent\u3002");
}
function toOperationPlan(payload) {
  if (typeof payload.planId !== "string" || typeof payload.summary !== "string" || !Array.isArray(payload.operations) || !isRisk(payload.risk)) {
    throw new AgentError("\u672C\u5730 Agent \u8FD4\u56DE\u4E86\u65E0\u6CD5\u8BC6\u522B\u7684\u64CD\u4F5C\u8BA1\u5212\u3002");
  }
  return {
    planId: payload.planId,
    createdAt: optionalString(payload.createdAt),
    expiresAt: optionalString(payload.expiresAt),
    integrityHash: optionalString(payload.integrityHash),
    expectedHashes: isStringMap(payload.expectedHashes) ? payload.expectedHashes : void 0,
    requiresConfirmation: payload.requiresConfirmation !== false,
    confirmationToken: optionalString(payload.confirmationToken),
    status: optionalString(payload.status),
    managedBy: "local-agent",
    summary: payload.summary,
    risk: payload.risk,
    operations: payload.operations
  };
}
async function localAgentRequest(settings, path, body) {
  const port = localAgentPort(settings);
  if (!port || !settings.localAgentToken) throw new AgentError("\u672C\u5730 Agent \u5C1A\u672A\u8FDE\u63A5\u3002");
  const response = await (0, import_obsidian2.requestUrl)({
    url: `http://127.0.0.1:${port}${path}`,
    method: body === void 0 ? "GET" : "POST",
    headers: {
      "X-Agent-Token": settings.localAgentToken,
      ...body === void 0 ? {} : { "Content-Type": "application/json" }
    },
    body: body === void 0 ? void 0 : JSON.stringify(body),
    throw: false
  });
  if (response.status < 200 || response.status >= 300) {
    const payload = response.json;
    const message = isRecord(payload) && typeof payload.error === "string" ? payload.error : `\u672C\u5730 Agent \u8BF7\u6C42\u5931\u8D25\uFF08HTTP ${response.status}\uFF09\u3002`;
    throw new AgentError(message);
  }
  return response.json;
}
function localAgentPort(settings) {
  const port = Number(settings.localAgentPort);
  return Number.isInteger(port) && port > 0 && port <= 65535 ? port : null;
}
function candidatePorts(settings) {
  const result = [];
  const configured = localAgentPort(settings);
  if (configured) result.push(configured);
  for (let port = 8765; port <= 8785; port += 1) if (!result.includes(port)) result.push(port);
  return result;
}
function vaultBasePath(app) {
  var _a;
  const adapter = app.vault.adapter;
  const path = (_a = adapter.getBasePath) == null ? void 0 : _a.call(adapter);
  if (!path) throw new AgentError("\u5F53\u524D\u5E73\u53F0\u65E0\u6CD5\u8BFB\u53D6 Vault \u6839\u76EE\u5F55\u3002");
  return path;
}
function operationResultText(value) {
  if (!isRecord(value)) return "\u64CD\u4F5C\u5DF2\u5B8C\u6210\u3002";
  if (typeof value.message === "string") return value.message;
  const type = typeof value.type === "string" ? value.type : "operation";
  const path = typeof value.path === "string" ? `\uFF1A${value.path}` : "";
  return `\u5DF2\u6267\u884C ${type}${path}`;
}
async function responseError(response, fallback) {
  try {
    const value = await response.json();
    return isRecord(value) && typeof value.error === "string" ? value.error : fallback;
  } catch (e) {
    return fallback;
  }
}
function withTimeout(promise, ms) {
  return Promise.race([promise, new Promise((_resolve, reject) => window.setTimeout(() => reject(new Error("timeout")), ms))]);
}
function isConversationSummary(value) {
  return isRecord(value) && typeof value.id === "string" && typeof value.title === "string" && (value.mode === "default" || value.mode === "plan");
}
function isConversationMessage(value) {
  return isRecord(value) && typeof value.id === "string" && (value.role === "user" || value.role === "assistant") && typeof value.text === "string" && typeof value.created_at === "number";
}
function isAgentEvent(value) {
  return isRecord(value) && typeof value.kind === "string" && typeof value.text === "string" && isRecord(value.data);
}
function isLocalAgentTool(value) {
  return isRecord(value) && typeof value.name === "string" && typeof value.description === "string";
}
function isRisk(value) {
  return value === "low" || value === "medium" || value === "high";
}
function optionalString(value) {
  return typeof value === "string" && value ? value : void 0;
}
function isStringMap(value) {
  return isRecord(value) && Object.values(value).every((item) => typeof item === "string");
}
function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// apps/obsidian-plugin/src/api/operation-plan.ts
function describeOperation(operation) {
  if (operation.type === "create-note") return `\u521B\u5EFA\u7B14\u8BB0\uFF1A${operation.path}`;
  if (operation.type === "update-note") return `\u7CBE\u786E\u66FF\u6362\uFF1A${operation.path}`;
  if (operation.type === "move-note") return `\u79FB\u52A8\u7B14\u8BB0\uFF1A${operation.path} \u2192 ${operation.targetPath}`;
  if (operation.type === "trash-note") return `\u79FB\u5165\u5E9F\u7EB8\u7BD3\uFF1A${operation.path}`;
  if (operation.type === "create-folder") return `\u521B\u5EFA\u76EE\u5F55\uFF1A${operation.path}`;
  if (operation.type === "delete-folder") return `\u5220\u9664\u7A7A\u76EE\u5F55\uFF1A${operation.path}`;
  if (operation.type === "update-metadata") return `\u66F4\u65B0\u5143\u6570\u636E\uFF1A${operation.path}`;
  return `\u8FFD\u52A0\u4EFB\u52A1\uFF1A${operation.path} - ${operation.title}`;
}

// apps/obsidian-plugin/src/views/assistant-view/assistant-view.ts
var AGENT_VIEW_TYPE = "personal-knowledge-agent-view";
var AssistantView = class extends import_obsidian3.ItemView {
  constructor(leaf, agentPlugin) {
    super(leaf);
    this.agentPlugin = agentPlugin;
    this.conversations = [];
    this.activeConversationId = "";
    this.messages = [];
    this.traces = [];
    this.approvals = [];
    this.agentPlan = null;
    this.operationPlan = null;
    this.mode = "default";
    this.queryScope = "current";
    this.requestVersion = 0;
    this.loaded = false;
  }
  getViewType() {
    return AGENT_VIEW_TYPE;
  }
  getDisplayText() {
    return "\u4E2A\u4EBA\u77E5\u8BC6\u5E93 Agent";
  }
  getIcon() {
    return "bot";
  }
  async onOpen() {
    this.buildShell();
    await this.reload();
    this.registerInterval(window.setInterval(() => {
      if (!this.loaded && this.agentPlugin.settings.localAgentToken) void this.reload();
    }, 3e3));
  }
  async onClose() {
    this.requestVersion += 1;
  }
  buildShell() {
    const root = this.containerEl.children[1];
    root.empty();
    root.addClass("pka-agent-view");
    const header = root.createDiv({ cls: "pka-agent-header" });
    const title = header.createDiv({ cls: "pka-agent-title" });
    const icon = title.createSpan({ cls: "pka-agent-title-icon" });
    (0, import_obsidian3.setIcon)(icon, "bot");
    title.createSpan({ text: "\u77E5\u8BC6\u5E93 Agent" });
    this.statusEl = header.createDiv({ cls: "pka-agent-status", text: "\u6B63\u5728\u8FDE\u63A5\u2026" });
    const sessionBar = root.createDiv({ cls: "pka-session-bar" });
    this.sessionSelect = sessionBar.createEl("select", { attr: { "aria-label": "\u5F53\u524D\u4F1A\u8BDD" } });
    this.sessionSelect.onchange = () => void this.selectConversation(this.sessionSelect.value);
    this.iconButton(sessionBar, "plus", "\u65B0\u5EFA\u4F1A\u8BDD", () => void this.newConversation());
    this.iconButton(sessionBar, "archive", "\u5F52\u6863\u4F1A\u8BDD", () => void this.archiveCurrent());
    const controls = root.createDiv({ cls: "pka-agent-controls" });
    this.scopeSelect = controls.createEl("select", { attr: { "aria-label": "\u77E5\u8BC6\u8303\u56F4" } });
    this.scopeSelect.createEl("option", { value: "current", text: "\u5F53\u524D\u7B14\u8BB0" });
    this.scopeSelect.createEl("option", { value: "vault", text: "\u6574\u4E2A\u77E5\u8BC6\u5E93" });
    this.scopeSelect.value = this.queryScope;
    this.scopeSelect.onchange = () => this.queryScope = this.scopeSelect.value;
    this.modeSelect = controls.createEl("select", { attr: { "aria-label": "Agent \u6A21\u5F0F" } });
    this.modeSelect.createEl("option", { value: "default", text: "\u6267\u884C\u6A21\u5F0F" });
    this.modeSelect.createEl("option", { value: "plan", text: "\u89C4\u5212\u6A21\u5F0F" });
    this.modeSelect.onchange = () => this.mode = this.modeSelect.value;
    this.planEl = root.createDiv({ cls: "pka-agent-plan" });
    this.timelineEl = root.createDiv({ cls: "pka-agent-timeline" });
    this.approvalEl = root.createDiv({ cls: "pka-agent-approvals" });
    this.operationEl = root.createDiv({ cls: "pka-operation-panel" });
    const composer = root.createDiv({ cls: "pka-agent-composer" });
    this.inputEl = composer.createEl("textarea", {
      attr: { placeholder: "\u8BE2\u95EE\u6216\u6574\u7406\u4F60\u7684\u77E5\u8BC6\u5E93\u2026", rows: "3", "aria-label": "\u6D88\u606F" }
    });
    this.inputEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        void this.send();
      }
    });
    const sendButton = composer.createEl("button", { cls: "pka-send-button", attr: { "aria-label": "\u53D1\u9001" } });
    (0, import_obsidian3.setIcon)(sendButton, "send-horizontal");
    sendButton.onclick = () => void this.send();
  }
  iconButton(parent, iconName, label, action) {
    const button = parent.createEl("button", { cls: "pka-icon-button", attr: { "aria-label": label, title: label } });
    (0, import_obsidian3.setIcon)(button, iconName);
    button.onclick = action;
  }
  async reload() {
    var _a, _b;
    if (!this.agentPlugin.settings.localAgentToken) {
      this.setStatus("\u8BF7\u5148\u542F\u52A8\u5E76\u8FDE\u63A5 local-agent", true);
      return;
    }
    try {
      this.conversations = await listConversations(this.agentPlugin.settings);
      const preferred = this.agentPlugin.settings.activeConversationId;
      const active = (_b = (_a = this.conversations.find((item) => item.id === preferred)) != null ? _a : this.conversations[0]) != null ? _b : await createConversation(this.agentPlugin.settings);
      if (!this.conversations.some((item) => item.id === active.id)) this.conversations.unshift(active);
      await this.selectConversation(active.id);
      this.loaded = true;
      this.setStatus("\u5DF2\u8FDE\u63A5");
    } catch (error) {
      this.loaded = false;
      this.setStatus(errorText(error), true);
    }
  }
  async selectConversation(id) {
    var _a;
    if (!id) return;
    const version = ++this.requestVersion;
    try {
      const detail = await loadConversation(this.agentPlugin.settings, id);
      if (version !== this.requestVersion) return;
      this.activeConversationId = id;
      this.mode = detail.session.mode;
      this.modeSelect.value = this.mode;
      this.agentPlan = detail.session.plan;
      this.operationPlan = (_a = detail.pendingOperationPlan) != null ? _a : null;
      this.messages = detail.messages.map((message) => ({
        id: message.id,
        role: message.role,
        text: message.text,
        createdAt: message.created_at
      }));
      this.traces = [];
      this.approvals = [];
      await this.agentPlugin.setActiveConversation(id);
      this.renderSessions();
      this.renderAll();
    } catch (error) {
      this.setStatus(errorText(error), true);
    }
  }
  async newConversation() {
    try {
      const conversation = await createConversation(this.agentPlugin.settings);
      this.conversations.unshift(conversation);
      await this.selectConversation(conversation.id);
    } catch (error) {
      new import_obsidian3.Notice(errorText(error));
    }
  }
  async archiveCurrent() {
    var _a;
    if (!this.activeConversationId) return;
    try {
      await archiveConversation(this.agentPlugin.settings, this.activeConversationId);
      this.conversations = this.conversations.filter((item) => item.id !== this.activeConversationId);
      const next = (_a = this.conversations[0]) != null ? _a : await createConversation(this.agentPlugin.settings);
      if (!this.conversations.length) this.conversations.push(next);
      await this.selectConversation(next.id);
    } catch (error) {
      new import_obsidian3.Notice(errorText(error));
    }
  }
  renderSessions() {
    this.sessionSelect.empty();
    for (const conversation of this.conversations) {
      this.sessionSelect.createEl("option", {
        value: conversation.id,
        text: conversation.title || "\u65B0\u4F1A\u8BDD"
      });
    }
    this.sessionSelect.value = this.activeConversationId;
  }
  async send() {
    const text = this.inputEl.value.trim();
    if (!text || !this.activeConversationId) return;
    const version = ++this.requestVersion;
    this.inputEl.value = "";
    this.approvals = [];
    this.messages.push({ id: crypto.randomUUID(), role: "user", text, createdAt: Date.now() });
    this.traces.push({ startedAt: Date.now(), tools: [] });
    this.setStatus("\u5904\u7406\u4E2D\u2026");
    this.renderAll();
    try {
      await streamConversation(
        this.app,
        this.agentPlugin.settings,
        this.activeConversationId,
        text,
        this.mode,
        this.queryScope,
        (event) => {
          if (version === this.requestVersion) this.handleEvent(event);
        }
      );
    } catch (error) {
      if (version !== this.requestVersion) return;
      this.messages.push({ id: crypto.randomUUID(), role: "error", text: errorText(error), createdAt: Date.now() });
      this.finishTrace();
      this.setStatus("\u8BF7\u6C42\u5931\u8D25", true);
      this.renderAll();
    }
  }
  handleEvent(event) {
    const trace = this.traces[this.traces.length - 1];
    if (event.kind === "turn_started") {
      trace.submissionId = numberValue(event.data.submission_id);
      const mode = event.data.mode;
      if (mode === "default" || mode === "plan") this.mode = mode;
    } else if (event.kind === "assistant_message") {
      this.updateAssistantMessage(event);
    } else if (event.kind === "tool_call") {
      trace.tools.push({
        id: stringValue(event.data.call_id) || crypto.randomUUID(),
        name: event.text,
        arguments: event.data.arguments,
        state: "running"
      });
    } else if (event.kind === "tool_result") {
      const callId = stringValue(event.data.call_id);
      const tool = trace.tools.find((item) => item.id === callId);
      if (tool) tool.state = event.data.status === "interrupted" ? "interrupted" : event.data.is_error ? "error" : "success";
    } else if (event.kind === "approval_requested") {
      this.approvals.push({
        callId: stringValue(event.data.call_id),
        submissionId: numberValue(event.data.submission_id),
        name: stringValue(event.data.name),
        command: stringValue(event.data.command),
        justification: stringValue(event.data.justification)
      });
    } else if (event.kind === "plan_updated" && Array.isArray(event.data.plan)) {
      this.agentPlan = {
        explanation: event.text || void 0,
        plan: event.data.plan
      };
    } else if (event.kind === "operation_plan" && isRecord2(event.data.plan)) {
      this.operationPlan = toOperationPlan(event.data.plan);
      if (!this.operationPlan.requiresConfirmation) void this.executeCurrentPlan();
    } else if (event.kind === "error") {
      this.messages.push({ id: crypto.randomUUID(), role: "error", text: event.text, createdAt: Date.now() });
      this.finishTrace();
      this.setStatus("\u8BF7\u6C42\u5931\u8D25", true);
    } else if (["turn_finished", "turn_interrupted", "shutdown"].includes(event.kind)) {
      this.finishTrace();
      this.setStatus(event.kind === "turn_interrupted" ? "\u5DF2\u4E2D\u65AD" : "\u5DF2\u8FDE\u63A5");
      void this.refreshConversationSummaries();
    }
    this.renderAll();
  }
  updateAssistantMessage(event) {
    let message = [...this.messages].reverse().find((item) => item.role === "assistant" && item.streaming);
    if (!message) {
      message = { id: crypto.randomUUID(), role: "assistant", text: "", createdAt: Date.now(), streaming: true };
      this.messages.push(message);
    }
    if (event.data.replace) message.text = event.text;
    else message.text += event.text;
    if (event.data.is_final) message.streaming = false;
  }
  finishTrace() {
    const trace = this.traces[this.traces.length - 1];
    if (trace && !trace.completedAt) {
      trace.completedAt = Date.now();
      for (const tool of trace.tools) if (tool.state === "running") tool.state = "interrupted";
    }
    for (const message of this.messages) message.streaming = false;
  }
  async refreshConversationSummaries() {
    try {
      this.conversations = await listConversations(this.agentPlugin.settings);
      this.renderSessions();
    } catch (e) {
    }
  }
  renderAll() {
    this.renderPlan();
    this.renderTimeline();
    this.renderApprovals();
    this.renderOperationPlan();
  }
  renderPlan() {
    this.planEl.empty();
    if (!this.agentPlan) {
      this.planEl.hide();
      return;
    }
    this.planEl.show();
    const header = this.planEl.createDiv({ cls: "pka-panel-title", text: "Agent \u5DE5\u4F5C\u8BA1\u5212" });
    const completed = this.agentPlan.plan.filter((item) => item.status === "completed").length;
    header.createSpan({ cls: "pka-plan-count", text: `${completed}/${this.agentPlan.plan.length}` });
    if (this.agentPlan.explanation) this.planEl.createDiv({ cls: "pka-plan-explanation", text: this.agentPlan.explanation });
    const list = this.planEl.createEl("ol");
    for (const item of this.agentPlan.plan) {
      list.createEl("li", { cls: `is-${item.status}`, text: item.step });
    }
  }
  renderTimeline() {
    this.timelineEl.empty();
    const entries = [
      ...this.messages.map((value) => ({ at: value.createdAt, kind: "message", value })),
      ...this.traces.map((value) => ({ at: value.startedAt, kind: "trace", value }))
    ];
    entries.sort((left, right) => left.at - right.at);
    if (!entries.length) {
      this.timelineEl.createDiv({ cls: "pka-empty-state", text: "\u9009\u62E9\u77E5\u8BC6\u8303\u56F4\uFF0C\u7136\u540E\u5F00\u59CB\u5BF9\u8BDD\u3002" });
      return;
    }
    for (const entry of entries) {
      if (entry.kind === "trace") this.renderTrace(entry.value);
      else void this.renderMessage(entry.value);
    }
    this.timelineEl.scrollTop = this.timelineEl.scrollHeight;
  }
  async renderMessage(message) {
    var _a, _b;
    const row = this.timelineEl.createDiv({ cls: `pka-message is-${message.role}` });
    const body = row.createDiv({ cls: "pka-message-body" });
    if (message.role === "assistant") {
      await import_obsidian3.MarkdownRenderer.render(this.app, message.text || "\u6B63\u5728\u601D\u8003\u2026", body, (_b = (_a = this.app.workspace.getActiveFile()) == null ? void 0 : _a.path) != null ? _b : "", this);
    } else {
      body.setText(message.text);
    }
  }
  renderTrace(trace) {
    var _a;
    const details = this.timelineEl.createEl("details", { cls: "pka-run-trace" });
    details.open = !trace.completedAt;
    const elapsed = Math.max(0, Math.round((((_a = trace.completedAt) != null ? _a : Date.now()) - trace.startedAt) / 1e3));
    details.createEl("summary", { text: trace.completedAt ? `\u5DF2\u5904\u7406 ${elapsed}s` : `\u6B63\u5728\u5904\u7406 ${elapsed}s` });
    if (!trace.tools.length) {
      details.createDiv({ cls: "pka-tool-row", text: trace.completedAt ? "\u672A\u8C03\u7528\u5DE5\u5177" : "\u6B63\u5728\u601D\u8003\u2026" });
      return;
    }
    for (const tool of trace.tools) {
      const row = details.createDiv({ cls: `pka-tool-row is-${tool.state}` });
      row.createSpan({ cls: "pka-tool-state", text: tool.state === "running" ? "\u25CB" : tool.state === "success" ? "\u2713" : "!" });
      row.createEl("code", { text: tool.name });
      if (tool.arguments !== void 0) row.createEl("pre", { text: JSON.stringify(tool.arguments, null, 2) });
    }
  }
  renderApprovals() {
    this.approvalEl.empty();
    for (const approval of this.approvals) {
      const card = this.approvalEl.createDiv({ cls: "pka-approval-card" });
      card.createDiv({ cls: "pka-panel-title", text: `\u6743\u9650\u8BF7\u6C42\uFF1A${approval.name}` });
      if (approval.command) card.createEl("code", { text: approval.command });
      if (approval.justification) card.createDiv({ text: approval.justification });
      const actions = card.createDiv({ cls: "pka-panel-actions" });
      this.actionButton(actions, "\u62D2\u7EDD", () => void this.answerApproval(approval, false));
      this.actionButton(actions, "\u5141\u8BB8\u4E00\u6B21", () => void this.answerApproval(approval, true), true);
    }
  }
  async answerApproval(approval, approved) {
    if (approval.resolving) return;
    approval.resolving = true;
    try {
      await resolveApproval(
        this.agentPlugin.settings,
        this.activeConversationId,
        approval.callId,
        approval.submissionId,
        approved
      );
      this.approvals = this.approvals.filter((item) => item !== approval);
      this.renderApprovals();
    } catch (error) {
      new import_obsidian3.Notice(errorText(error));
      approval.resolving = false;
    }
  }
  renderOperationPlan() {
    this.operationEl.empty();
    const plan = this.operationPlan;
    if (!plan) {
      this.operationEl.hide();
      return;
    }
    this.operationEl.show();
    const header = this.operationEl.createDiv({ cls: "pka-panel-title", text: "Vault \u4FEE\u6539\u9884\u89C8" });
    header.createSpan({ cls: `pka-risk is-${plan.risk}`, text: riskText(plan.risk) });
    this.operationEl.createDiv({ cls: "pka-operation-summary", text: plan.summary });
    const list = this.operationEl.createEl("ol");
    for (const operation of plan.operations) list.createEl("li", { text: describeOperation(operation) });
    if (plan.status) this.operationEl.createDiv({ cls: "pka-operation-status", text: `\u72B6\u6001\uFF1A${plan.status}` });
    const actions = this.operationEl.createDiv({ cls: "pka-panel-actions" });
    if (!plan.status || plan.status === "pending") {
      this.actionButton(actions, plan.requiresConfirmation ? "\u786E\u8BA4\u6267\u884C" : "\u6B63\u5728\u81EA\u52A8\u6267\u884C", () => void this.executeCurrentPlan(), true, !plan.requiresConfirmation);
    }
    if (plan.status === "succeeded" && plan.rollbackToken) {
      this.actionButton(actions, "\u64A4\u9500", () => void this.rollbackCurrentPlan());
    }
  }
  actionButton(parent, text, action, primary = false, disabled = false) {
    const button = parent.createEl("button", { cls: primary ? "mod-cta" : "", text });
    button.disabled = disabled;
    button.onclick = action;
  }
  async executeCurrentPlan() {
    const plan = this.operationPlan;
    if (!plan || plan.status === "running" || plan.status === "succeeded") return;
    plan.status = "running";
    this.renderOperationPlan();
    try {
      const results = await this.agentPlugin.executePlan(plan);
      plan.status = "succeeded";
      this.messages.push({ id: crypto.randomUUID(), role: "notice", text: results.join("\n"), createdAt: Date.now() });
    } catch (error) {
      plan.status = "failed";
      this.messages.push({ id: crypto.randomUUID(), role: "error", text: errorText(error), createdAt: Date.now() });
    }
    this.renderAll();
  }
  async rollbackCurrentPlan() {
    const plan = this.operationPlan;
    if (!plan) return;
    try {
      const results = await this.agentPlugin.rollbackPlan(plan);
      plan.status = "rolled_back";
      this.messages.push({ id: crypto.randomUUID(), role: "notice", text: results.join("\n"), createdAt: Date.now() });
    } catch (error) {
      this.messages.push({ id: crypto.randomUUID(), role: "error", text: errorText(error), createdAt: Date.now() });
    }
    this.renderAll();
  }
  setStatus(text, error = false) {
    this.statusEl.setText(text);
    this.statusEl.toggleClass("is-error", error);
  }
};
function errorText(error) {
  return error instanceof Error ? error.message : "\u53D1\u751F\u672A\u77E5\u9519\u8BEF\u3002";
}
function riskText(risk) {
  return risk === "low" ? "\u4F4E\u98CE\u9669" : risk === "medium" ? "\u4E2D\u98CE\u9669" : "\u9AD8\u98CE\u9669";
}
function stringValue(value) {
  return typeof value === "string" ? value : "";
}
function numberValue(value) {
  return typeof value === "number" ? value : 0;
}
function isRecord2(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// apps/obsidian-plugin/src/obsidian/workspace-controller.ts
async function openAssistantView(app) {
  await app.workspace.ensureSideLeaf(AGENT_VIEW_TYPE, "right", {
    active: true,
    reveal: true
  });
}

// apps/obsidian-plugin/src/bootstrap/register-commands.ts
function registerCommands(plugin) {
  plugin.addRibbonIcon("bot", "\u6253\u5F00\u4E2A\u4EBA\u77E5\u8BC6\u5E93 Agent", () => {
    void openAssistantView(plugin.app);
  });
  plugin.addCommand({
    id: "open-personal-knowledge-agent",
    name: "\u6253\u5F00\u4E2A\u4EBA\u77E5\u8BC6\u5E93 Agent",
    callback: () => openAssistantView(plugin.app)
  });
}

// apps/obsidian-plugin/src/settings/settings.ts
var import_obsidian4 = require("obsidian");
var PROVIDERS = [
  {
    id: "deepseek",
    name: "DeepSeek",
    apiBaseUrl: "https://api.deepseek.com",
    models: ["deepseek-v4-flash", "deepseek-v4-pro"]
  },
  {
    id: "custom",
    name: "\u81EA\u5B9A\u4E49 OpenAI-compatible",
    apiBaseUrl: "https://api.openai.com/v1",
    models: []
  }
];
var DEFAULT_SETTINGS = {
  provider: "deepseek",
  apiBaseUrl: "https://api.deepseek.com",
  model: "deepseek-v4-flash",
  secretId: "personal-knowledge-agent-api-key",
  localAgentPort: "8765",
  localAgentToken: "",
  executionMode: "confirm_all",
  activeConversationId: ""
};
function providerById(id) {
  var _a;
  return (_a = PROVIDERS.find((provider) => provider.id === id)) != null ? _a : PROVIDERS[0];
}
var AgentSettingTab = class extends import_obsidian4.PluginSettingTab {
  constructor(app, agentPlugin) {
    super(app, agentPlugin);
    this.agentPlugin = agentPlugin;
  }
  display() {
    const { containerEl } = this;
    containerEl.empty();
    const provider = providerById(this.agentPlugin.settings.provider);
    new import_obsidian4.Setting(containerEl).setName("\u672C\u5730 Agent \u7AEF\u53E3").setDesc("local-agent HTTP \u7AEF\u53E3\uFF1B\u670D\u52A1\u4E0D\u53EF\u7528\u65F6\u63D2\u4EF6\u4F1A\u63D0\u793A\u91CD\u8FDE\uFF0C\u4E0D\u4F1A\u964D\u7EA7\u5230\u65E7\u6D41\u7A0B\u3002").addText(
      (text) => text.setPlaceholder("8765").setValue(this.agentPlugin.settings.localAgentPort).onChange(async (value) => {
        this.agentPlugin.settings.localAgentPort = value.trim();
        await this.agentPlugin.saveSettings();
      })
    );
    const localAgentSetting = new import_obsidian4.Setting(containerEl).setName("\u672C\u5730 Agent \u8FDE\u63A5\u6D4B\u8BD5").setDesc("\u81EA\u52A8\u53D1\u73B0\u672C\u5730 Agent\uFF0C\u53D1\u9001\u5F53\u524D Vault \u8DEF\u5F84\uFF0C\u5E76\u4FDD\u5B58\u8BBF\u95EE\u4EE4\u724C\u3002").addButton(
      (button) => button.setButtonText("\u81EA\u52A8\u8FDE\u63A5").onClick(async () => {
        button.setDisabled(true).setButtonText("\u8FDE\u63A5\u4E2D...");
        localAgentStatusEl.setText("\u8FDE\u63A5\u4E2D...");
        localAgentStatusEl.removeClass("is-success", "is-error");
        try {
          const result = await discoverLocalAgent(this.app, this.agentPlugin.settings);
          this.agentPlugin.settings.localAgentPort = result.port;
          this.agentPlugin.settings.localAgentToken = result.token;
          await this.agentPlugin.saveSettings();
          await updateLocalAgentPolicy(this.app, this.agentPlugin.settings);
          localAgentStatusEl.setText(`\u672C\u5730 Agent \u5DF2\u8FDE\u63A5\uFF1A${result.vaultRoot}`);
          localAgentStatusEl.addClass("is-success");
        } catch (error) {
          localAgentStatusEl.setText(error instanceof Error ? error.message : "\u672C\u5730 Agent \u4E0D\u53EF\u7528\u3002");
          localAgentStatusEl.addClass("is-error");
        } finally {
          button.setDisabled(false).setButtonText("\u81EA\u52A8\u8FDE\u63A5");
        }
      })
    ).addButton(
      (button) => button.setButtonText("\u6D4B\u8BD5").onClick(async () => {
        button.setDisabled(true).setButtonText("\u6D4B\u8BD5\u4E2D...");
        localAgentStatusEl.setText("\u6D4B\u8BD5\u4E2D...");
        localAgentStatusEl.removeClass("is-success", "is-error");
        try {
          await testLocalAgent(this.app, this.agentPlugin.settings);
          localAgentStatusEl.setText("\u672C\u5730 Agent \u9274\u6743\u548C\u5DE5\u5177\u63A5\u53E3\u53EF\u7528\u3002");
          localAgentStatusEl.addClass("is-success");
        } catch (error) {
          localAgentStatusEl.setText(error instanceof Error ? error.message : "\u672C\u5730 Agent \u4E0D\u53EF\u7528\u3002");
          localAgentStatusEl.addClass("is-error");
        } finally {
          button.setDisabled(false).setButtonText("\u6D4B\u8BD5");
        }
      })
    );
    const localAgentStatusEl = containerEl.createDiv({ cls: "pka-setting-status" });
    localAgentSetting.settingEl.insertAdjacentElement("afterend", localAgentStatusEl);
    new import_obsidian4.Setting(containerEl).setName("\u6267\u884C\u6743\u9650\u6A21\u5F0F").setDesc("\u8BF7\u6C42\u6279\u51C6\u6700\u5B89\u5168\uFF1B\u66FF\u6211\u5BA1\u6279\u4F1A\u81EA\u52A8\u6267\u884C\u767D\u540D\u5355\u4F4E\u98CE\u9669\u64CD\u4F5C\uFF1B\u5B8C\u5168\u8BBF\u95EE\u4E0D\u518D\u5F39\u51FA\u6267\u884C\u786E\u8BA4\u3002").addDropdown(
      (dropdown) => dropdown.addOption("confirm_all", "\u8BF7\u6C42\u6279\u51C6").addOption("risk_based", "\u66FF\u6211\u5BA1\u6279").addOption("unattended", "\u5B8C\u5168\u8BBF\u95EE\u6743\u9650").setValue(this.agentPlugin.settings.executionMode).onChange(async (value) => {
        if (!isExecutionMode(value)) return;
        this.agentPlugin.settings.executionMode = value;
        await this.agentPlugin.saveSettings();
        await updateLocalAgentPolicy(this.app, this.agentPlugin.settings);
      })
    );
    const toolsSetting = new import_obsidian4.Setting(containerEl).setName("\u5DE5\u5177\u5C55\u793A").setDesc("\u67E5\u770B\u672C\u5730 Agent \u5F53\u524D\u6CE8\u518C\u7684\u5DE5\u5177\u3002").addButton(
      (button) => button.setButtonText("\u5237\u65B0\u5DE5\u5177\u5217\u8868").onClick(async () => {
        button.setDisabled(true).setButtonText("\u5237\u65B0\u4E2D...");
        renderToolsPanel(toolsPanelEl, "loading");
        try {
          renderToolsPanel(toolsPanelEl, await listLocalAgentTools(this.agentPlugin.settings));
        } catch (error) {
          renderToolsPanel(
            toolsPanelEl,
            error instanceof Error ? error.message : "\u8BFB\u53D6\u5DE5\u5177\u5217\u8868\u5931\u8D25\u3002"
          );
        } finally {
          button.setDisabled(false).setButtonText("\u5237\u65B0\u5DE5\u5177\u5217\u8868");
        }
      })
    );
    const toolsPanelEl = containerEl.createDiv({ cls: "pka-tools-panel" });
    toolsSetting.settingEl.insertAdjacentElement("afterend", toolsPanelEl);
    renderToolsPanel(toolsPanelEl, []);
    new import_obsidian4.Setting(containerEl).setName("\u4F9B\u5E94\u5546").setDesc("DeepSeek \u9ED8\u8BA4\u4F7F\u7528\u5B98\u65B9 OpenAI-compatible API\u3002").addDropdown((dropdown) => {
      for (const item of PROVIDERS) {
        dropdown.addOption(item.id, item.name);
      }
      dropdown.setValue(provider.id).onChange(async (value) => {
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
      new import_obsidian4.Setting(containerEl).setName("API Base URL").setDesc("OpenAI-compatible API \u5730\u5740\uFF1B\u666E\u901A HTTP \u53EA\u5141\u8BB8\u672C\u673A\u5730\u5740\u3002").addText(
        (text) => text.setPlaceholder("https://api.openai.com/v1").setValue(this.agentPlugin.settings.apiBaseUrl).onChange(async (value) => {
          this.agentPlugin.settings.apiBaseUrl = value.trim();
          await this.agentPlugin.saveSettings();
        })
      );
    } else {
      new import_obsidian4.Setting(containerEl).setName("API Base URL").setDesc(provider.apiBaseUrl);
    }
    const modelSetting = new import_obsidian4.Setting(containerEl).setName("\u6A21\u578B").setDesc(provider.models.length ? "\u9009\u62E9\u5F53\u524D\u4F9B\u5E94\u5546\u652F\u6301\u7684\u6A21\u578B\u3002" : "\u586B\u5199\u670D\u52A1\u7AEF\u5B9E\u9645\u652F\u6301\u7684\u6A21\u578B\u540D\u79F0\u3002");
    if (provider.models.length) {
      modelSetting.addDropdown((dropdown) => {
        for (const model of provider.models) {
          dropdown.addOption(model, model);
        }
        dropdown.setValue(provider.models.includes(this.agentPlugin.settings.model) ? this.agentPlugin.settings.model : provider.models[0]).onChange(async (value) => {
          this.agentPlugin.settings.model = value;
          await this.agentPlugin.saveSettings();
        });
      });
    } else {
      modelSetting.addText(
        (text) => text.setPlaceholder("model-name").setValue(this.agentPlugin.settings.model).onChange(async (value) => {
          this.agentPlugin.settings.model = value.trim();
          await this.agentPlugin.saveSettings();
        })
      );
    }
    new import_obsidian4.Setting(containerEl).setName("API \u5BC6\u94A5").setDesc("\u4ECE Obsidian SecretStorage \u4E2D\u9009\u62E9\uFF1B\u672C\u5730\u65E0\u8BA4\u8BC1\u670D\u52A1\u53EF\u4EE5\u7559\u7A7A\u3002").addText(
      (text) => {
        var _a;
        return text.setPlaceholder("sk-...").setValue((_a = this.app.secretStorage.getSecret(this.agentPlugin.settings.secretId)) != null ? _a : "").onChange(async (value) => {
          this.app.secretStorage.setSecret(
            this.agentPlugin.settings.secretId,
            value.trim()
          );
          await this.agentPlugin.saveSettings();
        });
      }
    );
    const keyInput = containerEl.querySelector(
      ".setting-item:last-child input"
    );
    if (keyInput) keyInput.type = "password";
  }
};
function renderToolsPanel(containerEl, toolsOrMessage) {
  containerEl.empty();
  if (toolsOrMessage === "loading") {
    containerEl.createDiv({ cls: "pka-tools-empty", text: "\u6B63\u5728\u8BFB\u53D6\u5DE5\u5177\u5217\u8868..." });
    return;
  }
  if (typeof toolsOrMessage === "string") {
    containerEl.createDiv({ cls: "pka-tools-empty is-error", text: toolsOrMessage });
    return;
  }
  if (!toolsOrMessage.length) {
    containerEl.createDiv({ cls: "pka-tools-empty", text: "\u5C1A\u672A\u52A0\u8F7D\u5DE5\u5177\u5217\u8868\u3002" });
    return;
  }
  for (const tool of toolsOrMessage) {
    const card = containerEl.createDiv({ cls: "pka-tool-card" });
    const header = card.createDiv({ cls: "pka-tool-card-header" });
    header.createDiv({ cls: "pka-tool-name", text: tool.name });
    header.createDiv({ cls: "pka-tool-meta", text: toolMeta(tool).join(" \xB7 ") });
    card.createDiv({ cls: "pka-tool-description", text: tool.description });
    const inputs = inputNames(tool.input_schema);
    if (inputs) card.createDiv({ cls: "pka-tool-schema", text: `\u53C2\u6570\uFF1A${inputs}` });
  }
}
function toolMeta(tool) {
  return [
    tool.permission,
    tool.effect,
    tool.risk_level,
    tool.invocation_policy,
    tool.requires_confirmation ? "\u9700\u786E\u8BA4" : void 0,
    typeof tool.timeout_seconds === "number" ? `${tool.timeout_seconds}s` : void 0
  ].filter((item) => Boolean(item));
}
function inputNames(schema) {
  const properties = schema == null ? void 0 : schema.properties;
  return isRecord3(properties) ? Object.keys(properties).join(", ") : "";
}
function isRecord3(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function isExecutionMode(value) {
  return value === "confirm_all" || value === "risk_based" || value === "unattended";
}

// apps/obsidian-plugin/src/bootstrap/register-settings.ts
function registerSettings(plugin) {
  plugin.addSettingTab(new AgentSettingTab(plugin.app, plugin));
}

// apps/obsidian-plugin/src/bootstrap/register-views.ts
function registerViews(plugin) {
  plugin.registerView(
    AGENT_VIEW_TYPE,
    (leaf) => new AssistantView(leaf, plugin)
  );
}

// apps/obsidian-plugin/src/bootstrap/initialize-plugin.ts
function initializePlugin(plugin) {
  registerViews(plugin);
  registerSettings(plugin);
  registerCommands(plugin);
}

// apps/obsidian-plugin/src/main.ts
var LOCAL_AGENT_TOKEN_SECRET_ID = "personal-knowledge-agent-local-token";
var PersonalKnowledgeAgentPlugin = class extends import_obsidian5.Plugin {
  constructor() {
    super(...arguments);
    this.autoConnectTimer = null;
    this.autoConnectRunning = false;
  }
  async onload() {
    await this.loadSettings();
    initializePlugin(this);
    this.startLocalAgentAutoConnect();
  }
  onunload() {
    this.app.workspace.detachLeavesOfType(AGENT_VIEW_TYPE);
  }
  executePlan(plan) {
    return executeLocalOperationPlan(this.settings, plan);
  }
  rollbackPlan(plan) {
    return rollbackLocalOperationPlan(this.settings, plan);
  }
  async setActiveConversation(id) {
    this.settings.activeConversationId = id;
    await this.saveSettings();
  }
  startLocalAgentAutoConnect() {
    const connect = () => void this.autoConnectLocalAgent();
    connect();
    this.autoConnectTimer = window.setInterval(connect, 5e3);
    this.registerInterval(this.autoConnectTimer);
  }
  async autoConnectLocalAgent() {
    if (this.autoConnectRunning) return;
    this.autoConnectRunning = true;
    try {
      const result = await discoverLocalAgent(this.app, this.settings);
      this.settings.localAgentPort = result.port;
      this.settings.localAgentToken = result.token;
      await this.saveSettings();
      await updateLocalAgentPolicy(this.app, this.settings);
      if (this.autoConnectTimer !== null) {
        window.clearInterval(this.autoConnectTimer);
        this.autoConnectTimer = null;
      }
    } catch (e) {
    } finally {
      this.autoConnectRunning = false;
    }
  }
  async saveSettings() {
    const { localAgentToken, ...settings } = this.settings;
    if (localAgentToken) this.app.secretStorage.setSecret(LOCAL_AGENT_TOKEN_SECRET_ID, localAgentToken);
    await this.saveData(settings);
  }
  async loadSettings() {
    var _a, _b;
    const data = await this.loadData();
    const value = typeof data === "object" && data !== null ? data : {};
    const oldApiBaseUrl = typeof value.apiBaseUrl === "string" ? value.apiBaseUrl : DEFAULT_SETTINGS.apiBaseUrl;
    const providerId = typeof value.provider === "string" ? value.provider : oldApiBaseUrl.includes("api.deepseek.com") ? "deepseek" : "custom";
    const provider = providerById(providerId);
    const model = typeof value.model === "string" && value.model ? value.model : (_a = provider.models[0]) != null ? _a : "";
    this.settings = {
      provider: provider.id,
      apiBaseUrl: provider.id === "custom" ? oldApiBaseUrl : provider.apiBaseUrl,
      model: provider.models.length && !provider.models.includes(model) ? provider.models[0] : model,
      secretId: typeof value.secretId === "string" && value.secretId ? value.secretId : DEFAULT_SETTINGS.secretId,
      localAgentPort: typeof value.localAgentPort === "string" ? value.localAgentPort : DEFAULT_SETTINGS.localAgentPort,
      localAgentToken: (_b = this.app.secretStorage.getSecret(LOCAL_AGENT_TOKEN_SECRET_ID)) != null ? _b : "",
      executionMode: isExecutionMode(value.executionMode) ? value.executionMode : DEFAULT_SETTINGS.executionMode,
      activeConversationId: typeof value.activeConversationId === "string" ? value.activeConversationId : ""
    };
  }
};
