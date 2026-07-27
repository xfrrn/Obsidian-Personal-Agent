import { App, MarkdownView, requestUrl } from "obsidian";
import type { OperationPlan } from "./operation-plan";
import type { AgentSettings } from "../settings/settings";
import { AgentError } from "../utils/protocol";
import { extractFileReferencePaths } from "../obsidian/file-references";

export type AgentMode = "default" | "plan";
export type QueryScope = "current" | "vault";
export type PlanStepStatus = "pending" | "in_progress" | "completed";

export interface AgentPlanState {
  explanation?: string;
  plan: Array<{ step: string; status: PlanStepStatus }>;
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  model: string;
  workspace: string;
  last_turn_state: string;
  mode: AgentMode;
  plan: AgentPlanState | null;
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  created_at: number;
}

export interface ConversationDetail {
  session: ConversationSummary;
  messages: ConversationMessage[];
  pendingOperationPlan?: OperationPlan;
}

export interface AgentEvent {
  kind: string;
  text: string;
  data: Record<string, unknown>;
}

export interface LocalAgentTool {
  name: string;
  description: string;
  permission?: string;
  risk_level?: string;
  effect?: string;
  invocation_policy?: string;
  requires_confirmation?: boolean;
  timeout_seconds?: number;
  input_schema?: Record<string, unknown>;
}

export async function listConversations(settings: AgentSettings): Promise<ConversationSummary[]> {
  const payload = await localAgentRequest(settings, "/api/sessions");
  if (!isRecord(payload) || !Array.isArray(payload.sessions)) {
    throw new AgentError("本地 Agent 返回了无法识别的会话列表。");
  }
  return payload.sessions.filter(isConversationSummary);
}

export async function createConversation(settings: AgentSettings): Promise<ConversationSummary> {
  const payload = await localAgentRequest(settings, "/api/sessions", {});
  if (!isConversationSummary(payload)) throw new AgentError("本地 Agent 无法创建会话。");
  return payload;
}

export async function loadConversation(
  settings: AgentSettings,
  sessionId: string
): Promise<ConversationDetail> {
  const payload = await localAgentRequest(settings, `/api/sessions/${encodeURIComponent(sessionId)}`);
  if (!isRecord(payload) || !isConversationSummary(payload.session) || !Array.isArray(payload.messages)) {
    throw new AgentError("本地 Agent 返回了无法识别的会话详情。");
  }
  return {
    session: payload.session,
    messages: payload.messages.filter(isConversationMessage),
    pendingOperationPlan: isRecord(payload.pendingOperationPlan)
      ? toOperationPlan(payload.pendingOperationPlan)
      : undefined
  };
}

export async function archiveConversation(settings: AgentSettings, sessionId: string): Promise<void> {
  await localAgentRequest(settings, `/api/sessions/${encodeURIComponent(sessionId)}/archive`, {});
}

export async function streamConversation(
  app: App,
  settings: AgentSettings,
  sessionId: string,
  text: string,
  mode: AgentMode,
  scope: QueryScope,
  onEvent: (event: AgentEvent) => void
): Promise<void> {
  const port = localAgentPort(settings);
  if (!port || !settings.localAgentToken) throw new AgentError("本地 Agent 尚未连接。");
  const activeFile = app.workspace.getActiveFile();
  const selectedText = app.workspace.getActiveViewOfType(MarkdownView)?.editor.getSelection();
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
        activeFilePath: activeFile?.path,
        selectedText: selectedText || undefined,
        referencedPaths: extractFileReferencePaths(app, text)
      }
    })
  });
  if (!response.ok || !response.body) {
    throw new AgentError(await responseError(response, "本地 Agent 流式请求失败。"));
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  while (true) {
    const { done, value } = await reader.read();
    pending += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const parsed = parseSseFrames(pending, done);
    pending = parsed.pending;
    for (const event of parsed.events) onEvent(event);
    if (done) return;
  }
}

export function parseSseFrames(source: string, flush = false): {
  events: AgentEvent[];
  pending: string;
} {
  const normalized = source.replace(/\r\n/g, "\n");
  const frames = normalized.split("\n\n");
  const pending = flush ? "" : frames.pop() ?? "";
  const events: AgentEvent[] = [];
  for (const frame of frames) {
    const data = frame.split("\n").find((line) => line.startsWith("data:"));
    if (!data) continue;
    const value: unknown = JSON.parse(data.slice(5).trim());
    if (isAgentEvent(value)) events.push(value);
  }
  if (flush && frames.length === 0 && normalized.trim()) {
    const data = normalized.split("\n").find((line) => line.startsWith("data:"));
    if (data) {
      const value: unknown = JSON.parse(data.slice(5).trim());
      if (isAgentEvent(value)) events.push(value);
    }
  }
  return { events, pending };
}

export async function resolveApproval(
  settings: AgentSettings,
  sessionId: string,
  callId: string,
  submissionId: number,
  approved: boolean
): Promise<void> {
  await localAgentRequest(settings, `/api/sessions/${encodeURIComponent(sessionId)}/approvals`, {
    callId,
    submissionId,
    approved
  });
}

export async function executeLocalOperationPlan(
  settings: AgentSettings,
  plan: OperationPlan
): Promise<string[]> {
  if (!plan.planId) throw new AgentError("操作计划缺少 ID。");
  const payload = await localAgentRequest(
    settings,
    `/operations/${encodeURIComponent(plan.planId)}/execute`,
    { confirmationToken: plan.confirmationToken }
  );
  if (!isRecord(payload) || !Array.isArray(payload.results)) {
    throw new AgentError("本地 Agent 返回了无法识别的执行结果。");
  }
  plan.rollbackToken = optionalString(payload.rollbackToken);
  plan.status = optionalString(payload.status) ?? "succeeded";
  return payload.results.map(operationResultText);
}

export async function rollbackLocalOperationPlan(
  settings: AgentSettings,
  plan: OperationPlan
): Promise<string[]> {
  if (!plan.planId) throw new AgentError("操作计划缺少 ID。");
  const payload = await localAgentRequest(
    settings,
    `/operations/${encodeURIComponent(plan.planId)}/rollback`,
    { rollbackToken: plan.rollbackToken }
  );
  if (!isRecord(payload) || !Array.isArray(payload.results)) {
    throw new AgentError("本地 Agent 返回了无法识别的撤销结果。");
  }
  plan.status = "rolled_back";
  return payload.results.map(() => `已撤销操作计划：${plan.planId}`);
}

export async function listLocalAgentTools(settings: AgentSettings): Promise<LocalAgentTool[]> {
  const payload = await localAgentRequest(settings, "/tools");
  if (!isRecord(payload) || !Array.isArray(payload.tools)) {
    throw new AgentError("本地 Agent 返回了无法识别的工具列表。");
  }
  return payload.tools.filter(isLocalAgentTool);
}

export async function updateLocalAgentPolicy(app: App, settings: AgentSettings): Promise<void> {
  if (!settings.localAgentToken) return;
  await localAgentRequest(settings, "/policy", {
    executionMode: settings.executionMode,
    llm: {
      baseUrl: settings.apiBaseUrl,
      model: settings.model.trim(),
      apiKey: app.secretStorage.getSecret(settings.secretId) ?? ""
    }
  });
}

export async function testLocalAgent(app: App, settings: AgentSettings): Promise<void> {
  const port = localAgentPort(settings);
  if (!port || !settings.localAgentToken) throw new AgentError("本地 Agent 尚未连接。");
  const response = await requestUrl({
    url: `http://127.0.0.1:${port}/identity`,
    method: "GET",
    headers: { "X-Agent-Token": settings.localAgentToken },
    throw: false
  });
  if (response.status < 200 || response.status >= 300) {
    throw new AgentError(`本地 Agent 不可用（HTTP ${response.status}）。`);
  }
  const payload = response.json as unknown;
  if (!isRecord(payload) || payload.vaultRoot !== vaultBasePath(app)) {
    throw new AgentError("本地 Agent 绑定的不是当前 Vault。");
  }
}

export async function discoverLocalAgent(
  app: App,
  settings: AgentSettings
): Promise<{ port: string; token: string; vaultRoot: string }> {
  const vaultPath = vaultBasePath(app);
  const ports = candidatePorts(settings);
  if (settings.localAgentToken) {
    for (const port of ports) {
      try {
        const response = await withTimeout(requestUrl({
          url: `http://127.0.0.1:${port}/identity`,
          method: "GET",
          headers: { "X-Agent-Token": settings.localAgentToken },
          throw: false
        }), 400);
        const payload = response.json as unknown;
        if (response.status >= 200 && response.status < 300 && isRecord(payload) && payload.vaultRoot === vaultPath) {
          return { port: String(port), token: settings.localAgentToken, vaultRoot: vaultPath };
        }
      } catch { /* try next port */ }
    }
  }
  for (const port of ports) {
    try {
      await withTimeout(requestUrl({ url: `http://127.0.0.1:${port}/health`, method: "GET", throw: false }), 400);
      const response = await withTimeout(requestUrl({
        url: `http://127.0.0.1:${port}/handshake`,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          vaultPath,
          executionMode: settings.executionMode,
          llm: {
            baseUrl: settings.apiBaseUrl,
            model: settings.model.trim(),
            apiKey: app.secretStorage.getSecret(settings.secretId) ?? ""
          }
        }),
        throw: false
      }), 1_500);
      const payload = response.json as unknown;
      if (response.status >= 200 && response.status < 300 && isRecord(payload) && typeof payload.token === "string") {
        return {
          port: String(port),
          token: payload.token,
          vaultRoot: typeof payload.vaultRoot === "string" ? payload.vaultRoot : vaultPath
        };
      }
    } catch { /* try next port */ }
  }
  throw new AgentError("没有发现可用的本地 Agent。请先启动 local-agent。");
}

export function toOperationPlan(payload: Record<string, unknown>): OperationPlan {
  if (typeof payload.planId !== "string" || typeof payload.summary !== "string" || !Array.isArray(payload.operations) || !isRisk(payload.risk)) {
    throw new AgentError("本地 Agent 返回了无法识别的操作计划。");
  }
  return {
    planId: payload.planId,
    createdAt: optionalString(payload.createdAt),
    expiresAt: optionalString(payload.expiresAt),
    integrityHash: optionalString(payload.integrityHash),
    expectedHashes: isStringMap(payload.expectedHashes) ? payload.expectedHashes : undefined,
    requiresConfirmation: payload.requiresConfirmation !== false,
    confirmationToken: optionalString(payload.confirmationToken),
    status: optionalString(payload.status),
    managedBy: "local-agent",
    summary: payload.summary,
    risk: payload.risk,
    operations: payload.operations as OperationPlan["operations"]
  };
}

async function localAgentRequest(
  settings: AgentSettings,
  path: string,
  body?: Record<string, unknown>
): Promise<unknown> {
  const port = localAgentPort(settings);
  if (!port || !settings.localAgentToken) throw new AgentError("本地 Agent 尚未连接。");
  const response = await requestUrl({
    url: `http://127.0.0.1:${port}${path}`,
    method: body === undefined ? "GET" : "POST",
    headers: {
      "X-Agent-Token": settings.localAgentToken,
      ...(body === undefined ? {} : { "Content-Type": "application/json" })
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    throw: false
  });
  if (response.status < 200 || response.status >= 300) {
    const payload = response.json as unknown;
    const message = isRecord(payload) && typeof payload.error === "string"
      ? payload.error
      : `本地 Agent 请求失败（HTTP ${response.status}）。`;
    throw new AgentError(message);
  }
  return response.json as unknown;
}

function localAgentPort(settings: AgentSettings): number | null {
  const port = Number(settings.localAgentPort);
  return Number.isInteger(port) && port > 0 && port <= 65535 ? port : null;
}

function candidatePorts(settings: AgentSettings): number[] {
  const result: number[] = [];
  const configured = localAgentPort(settings);
  if (configured) result.push(configured);
  for (let port = 8765; port <= 8785; port += 1) if (!result.includes(port)) result.push(port);
  return result;
}

function vaultBasePath(app: App): string {
  const adapter = app.vault.adapter as { getBasePath?: () => string };
  const path = adapter.getBasePath?.();
  if (!path) throw new AgentError("当前平台无法读取 Vault 根目录。");
  return path;
}

function operationResultText(value: unknown): string {
  if (!isRecord(value)) return "操作已完成。";
  if (typeof value.message === "string") return value.message;
  const type = typeof value.type === "string" ? value.type : "operation";
  const path = typeof value.path === "string" ? `：${value.path}` : "";
  return `已执行 ${type}${path}`;
}

async function responseError(response: Response, fallback: string): Promise<string> {
  try {
    const value: unknown = await response.json();
    return isRecord(value) && typeof value.error === "string" ? value.error : fallback;
  } catch {
    return fallback;
  }
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return Promise.race([promise, new Promise<T>((_resolve, reject) => window.setTimeout(() => reject(new Error("timeout")), ms))]);
}

function isConversationSummary(value: unknown): value is ConversationSummary {
  return isRecord(value) && typeof value.id === "string" && typeof value.title === "string" &&
    (value.mode === "default" || value.mode === "plan");
}

function isConversationMessage(value: unknown): value is ConversationMessage {
  return isRecord(value) && typeof value.id === "string" && (value.role === "user" || value.role === "assistant") &&
    typeof value.text === "string" && typeof value.created_at === "number";
}

function isAgentEvent(value: unknown): value is AgentEvent {
  return isRecord(value) && typeof value.kind === "string" && typeof value.text === "string" && isRecord(value.data);
}

function isLocalAgentTool(value: unknown): value is LocalAgentTool {
  return isRecord(value) && typeof value.name === "string" && typeof value.description === "string";
}

function isRisk(value: unknown): value is OperationPlan["risk"] {
  return value === "low" || value === "medium" || value === "high";
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value ? value : undefined;
}

function isStringMap(value: unknown): value is Record<string, string> {
  return isRecord(value) && Object.values(value).every((item) => typeof item === "string");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
