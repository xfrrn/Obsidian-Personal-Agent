import { Notice, Plugin, requestUrl } from "obsidian";
import {
  AgentSettingTab,
  AgentSettings,
  AgentSkill,
  ApprovalPolicy,
  DEFAULT_SETTINGS,
  McpServerStatus,
  SandboxMode,
  WebApiKeys,
  WebProviderName
} from "./settings";
import { normalizeAgentUrl, normalizeApiBaseUrl } from "./url";
import { CODEX_AGENT_VIEW_TYPE, CodeXAgentView } from "./codex-agent-view";
import { isThemeMode, ThemeMode } from "./theme";
import { agentLaunchSpec } from "./agent-process";
import {
  nextDailyRun,
  normalizeScheduledTasks,
  ScheduledTask
} from "./scheduled-tasks";

const API_KEY_SECRET_ID = "personal-knowledge-agent-api-key";
const WEB_API_KEY_SECRET_IDS: Record<WebProviderName, string> = {
  tavily: "personal-knowledge-agent-tavily-api-keys",
  exa: "personal-knowledge-agent-exa-api-keys",
  talordata: "personal-knowledge-agent-talordata-api-keys"
};
const AGENT_START_ATTEMPTS = 100;
const MAX_SKILL_IMPORT_FILES = 500;
const MAX_SKILL_IMPORT_BYTES = 10 * 1024 * 1024;
const SCHEDULE_CHECK_INTERVAL_MS = 60_000;

interface AgentChildProcess {
  stderr: { on(event: "data", listener: (chunk: { toString(): string }) => void): void } | null;
  kill(): boolean;
  once(event: "error", listener: (error: Error) => void): this;
  once(event: "exit", listener: (code: number | null) => void): this;
}

declare const require: (id: string) => unknown;
const { existsSync } = require("node:fs") as { existsSync(path: string): boolean };
const { spawn } = require("node:child_process") as {
  spawn(command: string, args: string[], options: {
    windowsHide: boolean;
    stdio: ["ignore", "ignore", "pipe"];
  }): AgentChildProcess;
};

export default class CodeXAgentPlugin extends Plugin {
  declare settings: AgentSettings;
  private apiKey = "";
  private webApiKeys: WebApiKeys = { tavily: "", exa: "", talordata: "" };
  private agentProcess: AgentChildProcess | null = null;
  private agentStartPromise: Promise<void> | null = null;
  private initialSyncPromise: Promise<void> | null = null;
  private agentStartError = "";
  private unloading = false;
  private scheduledTaskQueue: Promise<void> = Promise.resolve();
  private queuedScheduledTasks = new Map<string, Promise<void>>();
  private settingsSaveQueue: Promise<void> = Promise.resolve();

  async onload(): Promise<void> {
    await this.loadSettings();
    this.registerView(
      CODEX_AGENT_VIEW_TYPE,
      (leaf) => new CodeXAgentView(
        leaf,
        () => this.settings.agentUrl,
        () => this.syncAgentSettings(),
        () => ({ themeMode: this.settings.themeMode }),
        (settings) => this.persistPanelSettings(settings)
      )
    );
    this.addRibbonIcon("bot", "打开 CodeX Agent", () => void this.activateView());
    this.addCommand({
      id: "open-codex-agent",
      name: "打开 CodeX Agent",
      callback: () => void this.activateView()
    });
    this.addSettingTab(new AgentSettingTab(this.app, this));
    this.registerInterval(window.setInterval(
      () => this.queueDueScheduledTasks(),
      SCHEDULE_CHECK_INTERVAL_MS
    ));
    const initialSync = this.syncAgentSettings();
    this.initialSyncPromise = initialSync;
    void initialSync.then(() => this.queueDueScheduledTasks()).catch((error) => {
      if (!this.unloading) {
        new Notice(error instanceof Error ? error.message : "无法自动启动本地 Agent。");
      }
    }).finally(() => {
      if (this.initialSyncPromise === initialSync) this.initialSyncPromise = null;
    });
  }

  onunload(): void {
    this.unloading = true;
    this.stopLocalAgent();
    this.app.workspace.detachLeavesOfType(CODEX_AGENT_VIEW_TYPE);
  }

  getApiKey(): string {
    return this.apiKey;
  }

  getWebApiKeys(): WebApiKeys {
    return { ...this.webApiKeys };
  }

  async fetchModels(apiBaseUrl: string, apiKey: string): Promise<string[]> {
    const baseUrl = normalizeApiBaseUrl(apiBaseUrl);
    const response = await requestUrl({
      url: `${baseUrl}/models`,
      method: "GET",
      headers: apiKey.trim() ? { Authorization: `Bearer ${apiKey.trim()}` } : undefined,
      throw: false
    });
    const payload = response.json as {
      data?: Array<{ id?: unknown }>;
      error?: unknown;
    };
    if (response.status < 200 || response.status >= 300) {
      throw new Error(modelListError(payload.error, `获取模型失败：HTTP ${response.status}`));
    }
    const models = Array.from(new Set((payload.data ?? [])
      .map((model) => model.id)
      .filter((id): id is string => typeof id === "string" && id.trim().length > 0)))
      .sort((a, b) => a.localeCompare(b));
    if (!models.length) throw new Error("模型接口未返回可选模型。");
    return models;
  }

  async listSkills(): Promise<AgentSkill[]> {
    await this.ensureAgentReady();
    const response = await requestUrl({
      url: `${this.settings.agentUrl}/api/skills`,
      method: "GET",
      throw: false
    });
    const payload = response.json as { error?: unknown; skills?: AgentSkill[] };
    if (response.status < 200 || response.status >= 300) {
      throw new Error(typeof payload?.error === "string" ? payload.error : `读取 Skills 失败：HTTP ${response.status}`);
    }
    return Array.isArray(payload.skills)
      ? payload.skills.map((skill) => ({ ...skill, enabled: skill.enabled !== false }))
      : [];
  }

  async getLongTermMemory(): Promise<string> {
    await this.ensureAgentReady();
    const response = await requestUrl({
      url: `${this.settings.agentUrl}/api/memory`,
      method: "GET",
      throw: false
    });
    const payload = response.json as { error?: unknown; memory?: unknown };
    if (response.status < 200 || response.status >= 300) {
      throw new Error(typeof payload?.error === "string" ? payload.error : `读取长期记忆失败：HTTP ${response.status}`);
    }
    return typeof payload.memory === "string" ? payload.memory : "";
  }

  async updateLongTermMemory(memory: string): Promise<void> {
    await this.ensureAgentReady();
    const response = await requestUrl({
      url: `${this.settings.agentUrl}/api/memory`,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ memory }),
      throw: false
    });
    if (response.status < 200 || response.status >= 300) {
      const payload = response.json as { error?: unknown };
      throw new Error(typeof payload?.error === "string" ? payload.error : `保存长期记忆失败：HTTP ${response.status}`);
    }
  }

  async getMcpConfiguration(): Promise<{ path: string; content: string }> {
    await this.ensureAgentReady();
    const response = await requestUrl({
      url: `${this.settings.agentUrl}/api/mcp/config`,
      method: "GET",
      throw: false
    });
    const payload = response.json as { error?: unknown; path?: unknown; content?: unknown };
    if (response.status < 200 || response.status >= 300) {
      throw new Error(typeof payload?.error === "string" ? payload.error : `读取 MCP 配置失败：HTTP ${response.status}`);
    }
    return {
      path: typeof payload.path === "string" ? payload.path : "",
      content: typeof payload.content === "string" ? payload.content : ""
    };
  }

  async updateMcpConfiguration(content: string): Promise<void> {
    await this.ensureAgentReady();
    const response = await requestUrl({
      url: `${this.settings.agentUrl}/api/mcp/config`,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
      throw: false
    });
    if (response.status < 200 || response.status >= 300) {
      const payload = response.json as { error?: unknown };
      throw new Error(typeof payload?.error === "string" ? payload.error : `保存 MCP 配置失败：HTTP ${response.status}`);
    }
  }

  async listMcpServers(): Promise<McpServerStatus[]> {
    await this.ensureAgentReady();
    const response = await requestUrl({
      url: `${this.settings.agentUrl}/api/mcp/servers`,
      method: "GET",
      throw: false
    });
    const payload = response.json as { error?: unknown; servers?: unknown };
    if (response.status < 200 || response.status >= 300) {
      throw new Error(typeof payload?.error === "string" ? payload.error : `读取 MCP 状态失败：HTTP ${response.status}`);
    }
    return Array.isArray(payload.servers) ? payload.servers as McpServerStatus[] : [];
  }

  async loginMcpServer(server: string): Promise<string | null> {
    const payload = await this.mcpOAuthRequest("login", server) as { authorization_url?: unknown };
    return typeof payload.authorization_url === "string" ? payload.authorization_url : null;
  }

  async logoutMcpServer(server: string): Promise<void> {
    await this.mcpOAuthRequest("logout", server);
  }

  private async mcpOAuthRequest(action: "login" | "logout", server: string): Promise<object> {
    await this.ensureAgentReady();
    const response = await requestUrl({
      url: `${this.settings.agentUrl}/api/mcp/oauth/${action}`,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ server }),
      throw: false
    });
    const payload = response.json as { error?: unknown };
    if (response.status < 200 || response.status >= 300) {
      throw new Error(typeof payload?.error === "string" ? payload.error : `MCP OAuth 失败：HTTP ${response.status}`);
    }
    return payload;
  }

  async runScheduledTask(taskId: string): Promise<void> {
    return this.queueScheduledTask(taskId, true);
  }

  async isAgentRunning(): Promise<boolean> {
    if (this.initialSyncPromise) await this.initialSyncPromise.catch(() => undefined);
    return this.isAgentAvailable();
  }

  async importSkill(files: readonly File[]): Promise<AgentSkill> {
    if (!files.length || files.length > MAX_SKILL_IMPORT_FILES) {
      throw new Error(`Skill 必须包含 1 到 ${MAX_SKILL_IMPORT_FILES} 个文件。`);
    }
    if (files.reduce((total, file) => total + file.size, 0) > MAX_SKILL_IMPORT_BYTES) {
      throw new Error("Skill 文件总大小不能超过 10 MiB。");
    }
    await this.ensureAgentReady();
    const encodedFiles: Array<{ path: string; content: string }> = [];
    for (const file of files) {
      encodedFiles.push({
        path: file.webkitRelativePath || file.name,
        content: await readFileBase64(file)
      });
    }
    const response = await requestUrl({
      url: `${this.settings.agentUrl}/api/skills/import`,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ files: encodedFiles }),
      throw: false
    });
    const payload = response.json as { error?: unknown; skill?: AgentSkill };
    if (response.status < 200 || response.status >= 300 || !payload.skill) {
      throw new Error(typeof payload?.error === "string" ? payload.error : `导入 Skill 失败：HTTP ${response.status}`);
    }
    return payload.skill;
  }

  async persistPanelSettings(settings: { themeMode?: ThemeMode }): Promise<void> {
    this.settings = { ...this.settings, ...settings };
    await this.saveSettings();
  }

  async applySettings(value: AgentSettings, apiKey: string, webApiKeys: WebApiKeys): Promise<boolean> {
    const previousAgentUrl = this.settings.agentUrl;
    try {
      const workspace = value.workspace.trim();
      const model = value.model.trim();
      if (!workspace || !model) throw new Error("模型和 Agent 工作区不能为空。");
      this.settings = {
        ...value,
        agentUrl: normalizeAgentUrl(value.agentUrl),
        apiBaseUrl: normalizeApiBaseUrl(value.apiBaseUrl),
        model,
        workspace,
        disabledSkills: normalizeSkillNames(value.disabledSkills),
        sessionDbPath: value.sessionDbPath.trim(),
        scheduledTasks: mergeScheduledTaskRuntime(
          normalizeScheduledTasks(value.scheduledTasks),
          this.settings.scheduledTasks
        ),
        configured: true
      };
      this.apiKey = apiKey.trim();
      this.webApiKeys = normalizeWebApiKeys(webApiKeys);
      this.app.secretStorage.setSecret(API_KEY_SECRET_ID, this.apiKey);
      for (const provider of webProviderNames()) {
        this.app.secretStorage.setSecret(WEB_API_KEY_SECRET_IDS[provider], this.webApiKeys[provider]);
      }
      await this.saveSettings();
      if (this.settings.agentUrl !== previousAgentUrl) this.stopLocalAgent();
    } catch (error) {
      new Notice(error instanceof Error ? error.message : "无法保存 Agent 配置。");
      return false;
    }
    try {
      await this.syncAgentSettings(true);
      for (const leaf of this.app.workspace.getLeavesOfType(CODEX_AGENT_VIEW_TYPE)) {
        if (leaf.view instanceof CodeXAgentView) void leaf.view.refresh();
      }
      new Notice("Agent 配置已保存并应用。");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Agent 暂时不可用。";
      new Notice(`配置已保存，但尚未应用：${message}`);
    }
    return true;
  }

  private async syncAgentSettings(includeEmptyApiKey = false): Promise<void> {
    await this.ensureAgentStarted();
    const body: Record<string, unknown> = this.settings.configured ? {
        base_url: this.settings.apiBaseUrl,
        model: this.settings.model,
        workspace: this.settings.workspace,
        sandbox_mode: this.settings.sandboxMode,
        approval_policy: this.settings.approvalPolicy,
        shell_enabled: this.settings.shellEnabled,
        disabled_skills: this.settings.disabledSkills,
        session_db_path: this.settings.sessionDbPath,
        web_search_provider: "auto",
        web_fetch_provider: "auto",
        web_api_keys: {
          tavily: webKeyLines(this.webApiKeys.tavily),
          exa: webKeyLines(this.webApiKeys.exa),
          talordata: webKeyLines(this.webApiKeys.talordata)
        },
        confirmed: this.settings.sandboxMode === "danger-full-access"
      } : { workspace: this.settings.workspace };
    if (this.settings.configured && (this.apiKey || includeEmptyApiKey)) body.api_key = this.apiKey;
    const response = await requestUrl({
      url: `${this.settings.agentUrl}/api/config`,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      throw: false
    });
    if (response.status < 200 || response.status >= 300) {
      const payload = response.json as { error?: unknown };
      throw new Error(typeof payload?.error === "string" ? payload.error : `Agent 配置失败：HTTP ${response.status}`);
    }
  }

  private async activateView(): Promise<void> {
    await this.syncAgentSettings().catch(() => undefined);
    const existing = this.app.workspace.getLeavesOfType(CODEX_AGENT_VIEW_TYPE)[0];
    const leaf = existing ?? this.app.workspace.getRightLeaf(false);
    if (!leaf) return;
    if (!existing) await leaf.setViewState({ type: CODEX_AGENT_VIEW_TYPE, active: true });
    await this.app.workspace.revealLeaf(leaf);
  }

  private async ensureAgentReady(): Promise<void> {
    if (this.initialSyncPromise) await this.initialSyncPromise;
    else await this.ensureAgentStarted();
  }

  private async loadSettings(): Promise<void> {
    const saved = await this.loadData() as Partial<AgentSettings> | null;
    const adapter = this.app.vault.adapter as { getBasePath?: () => string };
    const configured = saved?.configured === true;
    let agentUrl = DEFAULT_SETTINGS.agentUrl;
    let apiBaseUrl = DEFAULT_SETTINGS.apiBaseUrl;
    try {
      agentUrl = normalizeAgentUrl(typeof saved?.agentUrl === "string" ? saved.agentUrl : agentUrl);
      apiBaseUrl = normalizeApiBaseUrl(typeof saved?.apiBaseUrl === "string" ? saved.apiBaseUrl : apiBaseUrl);
    } catch {
      // 损坏的本地设置回退到安全默认值，不能阻止插件启动。
    }
    this.settings = {
      agentUrl,
      apiBaseUrl,
      model: typeof saved?.model === "string" ? saved.model : DEFAULT_SETTINGS.model,
      workspace: configured && typeof saved?.workspace === "string" && saved.workspace.trim()
        ? saved.workspace
        : adapter.getBasePath?.() ?? "",
      sandboxMode: isSandboxMode(saved?.sandboxMode) ? saved.sandboxMode : DEFAULT_SETTINGS.sandboxMode,
      approvalPolicy: isApprovalPolicy(saved?.approvalPolicy) ? saved.approvalPolicy : DEFAULT_SETTINGS.approvalPolicy,
      shellEnabled: typeof saved?.shellEnabled === "boolean" ? saved.shellEnabled : DEFAULT_SETTINGS.shellEnabled,
      disabledSkills: normalizeSkillNames(saved?.disabledSkills),
      sessionDbPath: typeof saved?.sessionDbPath === "string" ? saved.sessionDbPath : DEFAULT_SETTINGS.sessionDbPath,
      themeMode: isThemeMode(saved?.themeMode) ? saved.themeMode : DEFAULT_SETTINGS.themeMode,
      scheduledTasks: normalizeScheduledTasks(saved?.scheduledTasks, true),
      configured
    };
    this.apiKey = this.app.secretStorage.getSecret(API_KEY_SECRET_ID) ?? "";
    this.webApiKeys = {
      tavily: this.app.secretStorage.getSecret(WEB_API_KEY_SECRET_IDS.tavily) ?? "",
      exa: this.app.secretStorage.getSecret(WEB_API_KEY_SECRET_IDS.exa) ?? "",
      talordata: this.app.secretStorage.getSecret(WEB_API_KEY_SECRET_IDS.talordata) ?? ""
    };
  }

  private queueDueScheduledTasks(): void {
    const now = Date.now();
    for (const task of this.settings.scheduledTasks) {
      if (task.enabled && task.nextRunAt <= now && !this.queuedScheduledTasks.has(task.id)) {
        void this.queueScheduledTask(task.id, false).catch((error) => {
          if (!this.unloading) {
            const message = error instanceof Error ? error.message : "未知错误";
            new Notice(`定时任务“${task.name}”失败：${message}`);
          }
        });
      }
    }
  }

  private queueScheduledTask(taskId: string, manual: boolean): Promise<void> {
    const queued = this.queuedScheduledTasks.get(taskId);
    if (queued) return queued;
    const run = this.scheduledTaskQueue.then(() => this.executeScheduledTask(taskId, manual));
    const tracked = run.finally(() => this.queuedScheduledTasks.delete(taskId));
    this.queuedScheduledTasks.set(taskId, tracked);
    this.scheduledTaskQueue = tracked.catch(() => undefined);
    return tracked;
  }

  private async executeScheduledTask(taskId: string, manual: boolean): Promise<void> {
    let task = this.settings.scheduledTasks.find(({ id }) => id === taskId);
    if (!task) throw new Error("定时任务不存在。");
    if (!manual && (!task.enabled || task.nextRunAt > Date.now())) return;

    const startedAt = Date.now();
    task = await this.updateScheduledTask(taskId, (current) => ({
      ...current,
      ...(!manual ? { nextRunAt: nextDailyRun(current.time, startedAt) } : {}),
      lastRun: { startedAt, status: "running" }
    }));
    try {
      await this.ensureAgentReady();
      if (!task.sessionId) {
        const response = await requestUrl({
          url: `${this.settings.agentUrl}/api/sessions`,
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
          throw: false
        });
        const payload = response.json as { id?: unknown; error?: unknown };
        if (response.status < 200 || response.status >= 300 || typeof payload.id !== "string") {
          throw new Error(typeof payload.error === "string" ? payload.error : `创建任务会话失败：HTTP ${response.status}`);
        }
        task = await this.updateScheduledTask(taskId, (current) => ({
          ...current,
          sessionId: payload.id as string
        }));
      }
      const response = await requestUrl({
        url: `${this.settings.agentUrl}/api/sessions/${encodeURIComponent(task.sessionId!)}/messages`,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: scheduledTaskPrompt(task),
          unattended: {
            access: task.permissions.access,
            allow_web: task.permissions.allowWeb
          }
        }),
        throw: false
      });
      const payload = response.json as {
        error?: unknown;
        events?: Array<{ kind?: unknown; text?: unknown }>;
      };
      if (response.status < 200 || response.status >= 300) {
        throw new Error(typeof payload.error === "string" ? payload.error : `执行任务失败：HTTP ${response.status}`);
      }
      const terminal = payload.events?.at(-1);
      if (terminal?.kind !== "turn_finished") {
        throw new Error(typeof terminal?.text === "string" && terminal.text ? terminal.text : "任务没有正常完成。");
      }
      await this.updateScheduledTask(taskId, (current) => ({
        ...current,
        lastRun: { startedAt, finishedAt: Date.now(), status: "success" }
      }));
    } catch (error) {
      const message = error instanceof Error ? error.message : "未知错误";
      await this.updateScheduledTask(taskId, (current) => ({
        ...current,
        lastRun: { startedAt, finishedAt: Date.now(), status: "failed", error: message }
      }));
      throw new Error(message);
    }
  }

  private async updateScheduledTask(
    taskId: string,
    update: (task: ScheduledTask) => ScheduledTask
  ): Promise<ScheduledTask> {
    let updated: ScheduledTask | undefined;
    this.settings = {
      ...this.settings,
      scheduledTasks: this.settings.scheduledTasks.map((task) => {
        if (task.id !== taskId) return task;
        updated = update(task);
        return updated;
      })
    };
    if (!updated) throw new Error("定时任务不存在。");
    await this.saveSettings();
    return updated;
  }

  private saveSettings(): Promise<void> {
    const snapshot = this.settings;
    const save = this.settingsSaveQueue.then(() => this.saveData(snapshot));
    this.settingsSaveQueue = save.catch(() => undefined);
    return save;
  }

  /** 已有服务直接复用；连接失败时只启动一个由插件托管的 Python 子进程。 */
  private async ensureAgentStarted(): Promise<void> {
    if (await this.isAgentAvailable()) return;
    this.agentStartPromise ??= this.startLocalAgent().finally(() => {
      this.agentStartPromise = null;
    });
    await this.agentStartPromise;
  }

  private async isAgentAvailable(): Promise<boolean> {
    try {
      const response = await requestUrl({
        url: `${this.settings.agentUrl}/api/config`,
        method: "GET",
        throw: false
      });
      return response.status >= 200 && response.status < 300;
    } catch {
      return false;
    }
  }

  /** 启动 Agent 并等待 HTTP 入口就绪，避免侧栏先显示一次断线页。 */
  private async startLocalAgent(): Promise<void> {
    if (!this.agentProcess) {
      const spec = agentLaunchSpec(this.settings.agentUrl, this.bundledAgentPath());
      this.agentStartError = "";
      const child = spawn(spec.command, spec.args, {
        windowsHide: true,
        stdio: ["ignore", "ignore", "pipe"]
      });
      this.agentProcess = child;
      child.stderr?.on("data", (chunk) => {
        this.agentStartError = `${this.agentStartError}${chunk.toString()}`.trim().slice(-1000);
      });
      child.once("error", (error) => {
        this.agentStartError = error.message;
        if (this.agentProcess === child) this.agentProcess = null;
      });
      child.once("exit", (code) => {
        if (!this.agentStartError) this.agentStartError = `Python 进程已退出（代码 ${code ?? "未知"}）`;
        if (this.agentProcess === child) this.agentProcess = null;
      });
    }

    for (let attempt = 0; attempt < AGENT_START_ATTEMPTS; attempt += 1) {
      if (await this.isAgentAvailable()) return;
      if (!this.agentProcess) break;
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    this.stopLocalAgent();
    const detail = this.agentStartError ? `：${this.agentStartError}` : "";
    throw new Error(`无法自动启动本地 Agent，请检查 Windows 一体包是否完整${detail}`);
  }

  /** 返回插件包内 Agent EXE；源码开发时不存在则回退到系统 Python。 */
  private bundledAgentPath(): string {
    const adapter = this.app.vault.adapter as { getFullPath?: (path: string) => string };
    if (!this.manifest.dir || !adapter.getFullPath) return "";
    const path = adapter.getFullPath(`${this.manifest.dir}/agent/codex-agent.exe`);
    return existsSync(path) ? path : "";
  }

  /** 只终止本插件创建的子进程，不影响用户手动运行的 Agent。 */
  private stopLocalAgent(): void {
    this.agentProcess?.kill();
    this.agentProcess = null;
  }
}

function isSandboxMode(value: unknown): value is SandboxMode {
  return value === "read-only" || value === "workspace-write" || value === "danger-full-access";
}

function isApprovalPolicy(value: unknown): value is ApprovalPolicy {
  return value === "never" || value === "on-request";
}

function normalizeSkillNames(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return Array.from(new Set(value.filter(
    (name): name is string => typeof name === "string" && /^[a-z0-9][a-z0-9-]*$/.test(name)
  ))).sort();
}

function scheduledTaskPrompt(task: ScheduledTask): string {
  return [
    "这是无人值守定时任务。请直接完成任务，不要向用户提问，也不要请求临时授权。",
    `任务名称：${task.name}`,
    "任务指令：",
    task.instruction,
    "完成后简要说明结果和修改过的文件；无法完成的部分请明确说明原因。"
  ].join("\n\n");
}

function mergeScheduledTaskRuntime(
  edited: ScheduledTask[],
  current: ScheduledTask[]
): ScheduledTask[] {
  return edited.map((task) => {
    const running = current.find(({ id }) => id === task.id);
    if (!running) return task;
    return {
      ...task,
      nextRunAt: task.time === running.time ? running.nextRunAt : task.nextRunAt,
      ...(running.sessionId ? { sessionId: running.sessionId } : {}),
      ...(running.lastRun ? { lastRun: running.lastRun } : {})
    };
  });
}

function normalizeWebApiKeys(value: WebApiKeys): WebApiKeys {
  return {
    tavily: webKeyLines(value.tavily).join("\n"),
    exa: webKeyLines(value.exa).join("\n"),
    talordata: webKeyLines(value.talordata).join("\n")
  };
}

function webKeyLines(value: string): string[] {
  return Array.from(new Set(value.split(/[\s,;]+/).map((key) => key.trim()).filter(Boolean)));
}

function webProviderNames(): WebProviderName[] {
  return ["tavily", "exa", "talordata"];
}

function modelListError(error: unknown, fallback: string): string {
  if (typeof error === "string") return error;
  if (error && typeof error === "object" && "message" in error) {
    const message = (error as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  return fallback;
}

function readFileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(reader.error ?? new Error(`无法读取文件: ${file.name}`));
    reader.readAsDataURL(file);
  });
}
