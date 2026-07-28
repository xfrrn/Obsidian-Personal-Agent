import { Notice, Plugin, requestUrl } from "obsidian";
import { AgentSettingTab, AgentSettings, AgentSkill, ApprovalPolicy, DEFAULT_SETTINGS, SandboxMode } from "./settings";
import { normalizeAgentUrl, normalizeApiBaseUrl } from "./url";
import { CODEX_AGENT_VIEW_TYPE, CodeXAgentView } from "./codex-agent-view";
import { isThemeMode, ThemeMode } from "./theme";
import { agentLaunchSpec } from "./agent-process";

const API_KEY_SECRET_ID = "personal-knowledge-agent-api-key";
const AGENT_START_ATTEMPTS = 100;
const MAX_SKILL_IMPORT_FILES = 500;
const MAX_SKILL_IMPORT_BYTES = 10 * 1024 * 1024;

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
  private agentProcess: AgentChildProcess | null = null;
  private agentStartPromise: Promise<void> | null = null;
  private initialSyncPromise: Promise<void> | null = null;
  private agentStartError = "";
  private unloading = false;

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
    const initialSync = this.syncAgentSettings();
    this.initialSyncPromise = initialSync;
    void initialSync.catch((error) => {
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
    return Array.isArray(payload.skills) ? payload.skills : [];
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
    await this.saveData(this.settings);
  }

  async applySettings(value: AgentSettings, apiKey: string): Promise<void> {
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
        sessionDbPath: value.sessionDbPath.trim(),
        configured: true
      };
      this.apiKey = apiKey.trim();
      this.app.secretStorage.setSecret(API_KEY_SECRET_ID, this.apiKey);
      await this.saveData(this.settings);
      if (this.settings.agentUrl !== previousAgentUrl) this.stopLocalAgent();
    } catch (error) {
      new Notice(error instanceof Error ? error.message : "无法保存 Agent 配置。");
      return;
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
        session_db_path: this.settings.sessionDbPath,
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
      sessionDbPath: typeof saved?.sessionDbPath === "string" ? saved.sessionDbPath : DEFAULT_SETTINGS.sessionDbPath,
      themeMode: isThemeMode(saved?.themeMode) ? saved.themeMode : DEFAULT_SETTINGS.themeMode,
      configured
    };
    this.apiKey = this.app.secretStorage.getSecret(API_KEY_SECRET_ID) ?? "";
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
