import { App, requestUrl } from "obsidian";
import type { QueryScope } from "../features/assistant/types";
import type { AgentSettings } from "../settings/settings";
import type { OperationPlan } from "../features/operation-preview/operation-plan";
import { AgentAnswer, AgentError, AgentTraceStep, chatCompletionsUrl, isTaskQuery } from "../utils/protocol";
import { collectTasks } from "../features/task-actions/list-tasks";
import { extractFileReferencePaths } from "../obsidian/vault-reader";

interface LocalTask {
  path: string;
  line: number;
  title: string;
  completed: boolean;
  heading?: string;
}

interface LocalSearchResult {
  path: string;
  title?: string;
  excerpt?: string;
}

export interface LocalAgentTool {
  name: string;
  description: string;
  permission?: string;
  risk_level?: string;
  timeout_seconds?: number;
  effect?: string;
  invocation_policy?: string;
  requires_confirmation?: boolean;
  input_schema?: Record<string, unknown>;
}

export type LocalAgentTraceHandler = (step: AgentTraceStep) => void;

export async function askLocalAgent(
  app: App,
  settings: AgentSettings,
  question: string,
  scope: QueryScope,
  onTrace?: LocalAgentTraceHandler
): Promise<AgentAnswer> {
  const port = localAgentPort(settings);
  if (!port) throw new AgentError("本地 Agent 端口未配置。");

  const body = await localChatBody(app, question, scope);
  if (onTrace && typeof fetch === "function") {
    return askLocalAgentStream(port, settings, body, onTrace);
  }
  const response = await requestUrl({
    url: `http://127.0.0.1:${port}/chat`,
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Agent-Token": settings.localAgentToken
    },
    body: JSON.stringify(body),
    throw: false
  });
  if (response.status < 200 || response.status >= 300) {
    throw new AgentError(`本地 Agent 请求失败（HTTP ${response.status}）。`);
  }
  return toAgentAnswer(response.json as unknown);
}

async function askLocalAgentStream(
  port: number,
  settings: AgentSettings,
  body: Record<string, unknown>,
  onTrace: LocalAgentTraceHandler
): Promise<AgentAnswer> {
  const response = await fetch(`http://127.0.0.1:${port}/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Agent-Token": settings.localAgentToken
    },
    body: JSON.stringify(body)
  });
  if (!response.ok) {
    throw new AgentError(`本地 Agent 流式请求失败（HTTP ${response.status}）。`);
  }
  if (!response.body) {
    throw new AgentError("当前环境不支持流式响应。");
  }

  let finalPayload: unknown;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const event = parseStreamEvent(part);
      if (!event) continue;
      if (event.event === "trace" && isTraceStep(event.data)) {
        onTrace({
          round: event.data.round,
          toolName: event.data.toolName ?? event.data.tool_name ?? "",
          status: event.data.status,
          summary: event.data.summary,
          detail: isRecord(event.data.detail) ? event.data.detail : undefined
        });
      } else if (event.event === "final") {
        finalPayload = event.data;
      } else if (event.event === "error") {
        const message = isRecord(event.data) && typeof event.data.error === "string"
          ? event.data.error
          : "本地 Agent 流式请求失败。";
        throw new AgentError(message);
      }
    }
  }
  if (!finalPayload) throw new AgentError("本地 Agent 没有返回最终结果。");
  return toAgentAnswer(finalPayload);
}

function parseStreamEvent(raw: string): { event: string; data: unknown } | null {
  const event = /^event:\s*(.+)$/m.exec(raw)?.[1]?.trim();
  const data = /^data:\s*(.+)$/m.exec(raw)?.[1];
  if (!event || !data) return null;
  return { event, data: JSON.parse(data) };
}

async function localChatBody(app: App, question: string, scope: QueryScope): Promise<Record<string, unknown>> {
  const activeFile = app.workspace.getActiveFile();
  const referencedPaths = extractFileReferencePaths(app, question);
  const body: Record<string, unknown> = {
    userInput: question,
    conversationId: "obsidian-plugin",
    scope,
    activeFilePath: activeFile?.path
  };
  const tasks = isTaskQuery(question) ? await collectTasks(app, scope) : null;
  const metadata: Record<string, unknown> = {};
  if (referencedPaths.length) metadata.referencedPaths = referencedPaths;
  if (tasks) metadata.tasks = tasks;
  if (Object.keys(metadata).length) body.metadata = metadata;
  return body;
}

export async function buildLocalOperationPlan(
  app: App,
  settings: AgentSettings,
  requestText: string,
  scope: QueryScope
): Promise<OperationPlan> {
  const port = localAgentPort(settings);
  if (!port) throw new AgentError("本地 Agent 端口未配置。");
  if (!settings.localAgentToken) throw new AgentError("本地 Agent 尚未配对。");
  const body = await localChatBody(app, requestText, scope);
  const response = await requestUrl({
    url: `http://127.0.0.1:${port}/chat`,
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Agent-Token": settings.localAgentToken
    },
    body: JSON.stringify(body),
    throw: false
  });
  if (response.status < 200 || response.status >= 300) {
    throw new AgentError(`本地 Agent 请求失败（HTTP ${response.status}）。`);
  }
  const output = unwrapToolOutput(response.json as unknown);
  if (!isRecord(output) || !isRecord(output.plan)) {
    throw new AgentError("本地 Agent 没有返回操作计划。");
  }
  return localPlanFromPayload(output.plan, agentTrace(response.json as unknown));
}

export async function listLocalAgentTools(settings: AgentSettings): Promise<LocalAgentTool[]> {
  const port = localAgentPort(settings);
  if (!port) throw new AgentError("本地 Agent 端口未配置。");
  if (!settings.localAgentToken) throw new AgentError("本地 Agent 尚未配对。");
  const response = await requestUrl({
    url: `http://127.0.0.1:${port}/tools`,
    method: "GET",
    headers: { "X-Agent-Token": settings.localAgentToken },
    throw: false
  });
  if (response.status < 200 || response.status >= 300) {
    throw new AgentError(`读取工具列表失败（HTTP ${response.status}）。`);
  }
  const payload = response.json as unknown;
  if (!isRecord(payload) || !Array.isArray(payload.tools)) {
    throw new AgentError("本地 Agent 返回了无法识别的工具列表。");
  }
  return payload.tools.filter(isLocalAgentTool);
}

export async function stageLocalOperationPlan(
  settings: AgentSettings,
  plan: OperationPlan,
  allowedPaths: string[]
): Promise<OperationPlan> {
  const payload = await localAgentRequest(settings, "/operations", {
    summary: plan.summary,
    operations: plan.operations,
    context: { source: "interactive", allowedPaths }
  });
  if (
    !isRecord(payload) ||
    typeof payload.planId !== "string" ||
    typeof payload.summary !== "string" ||
    !Array.isArray(payload.operations) ||
    !isRisk(payload.risk)
  ) {
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

function localPlanFromPayload(payload: Record<string, unknown>, trace: AgentTraceStep[] = []): OperationPlan {
  if (
    typeof payload.planId !== "string" ||
    typeof payload.summary !== "string" ||
    !Array.isArray(payload.operations) ||
    !isRisk(payload.risk)
  ) {
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
    trace,
    summary: payload.summary,
    risk: payload.risk,
    operations: payload.operations as OperationPlan["operations"]
  };
}

export async function executeLocalOperationPlan(
  settings: AgentSettings,
  plan: OperationPlan,
  confirmed: boolean
): Promise<string[]> {
  if (!plan.planId) throw new AgentError("操作计划缺少 ID。");
  const payload = await localAgentRequest(
    settings,
    `/operations/${encodeURIComponent(plan.planId)}/execute`,
    confirmed ? { confirmationToken: plan.confirmationToken } : {}
  );
  if (!isRecord(payload) || !Array.isArray(payload.results)) {
    throw new AgentError("本地 Agent 返回了无法识别的执行结果。");
  }
  plan.rollbackToken = optionalString(payload.rollbackToken);
  return payload.results.map(localOperationResultText);
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
  return payload.results.map(() => `已撤销操作计划：${plan.planId}`);
}

export async function updateLocalAgentPolicy(app: App, settings: AgentSettings): Promise<void> {
  if (!settings.localAgentToken) return;
  await localAgentRequest(settings, "/policy", {
    executionMode: settings.executionMode,
    intentLlm: intentLlmConfig(app, settings)
  });
}

export async function testLocalAgent(app: App, settings: AgentSettings): Promise<void> {
  const port = localAgentPort(settings);
  if (!port) throw new AgentError("本地 Agent 端口未配置。");
  if (!settings.localAgentToken) throw new AgentError("本地 Agent 尚未配对。");
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
        if (
          response.status >= 200 && response.status < 300 &&
          isRecord(payload) && payload.vaultRoot === vaultPath
        ) {
          return { port: String(port), token: settings.localAgentToken, vaultRoot: vaultPath };
        }
      } catch {
        // Try the next local port.
      }
    }
  }
  for (const port of ports) {
    try {
      await withTimeout(requestUrl({
        url: `http://127.0.0.1:${port}/health`,
        method: "GET",
        throw: false
      }), 400);
      const response = await withTimeout(requestUrl({
        url: `http://127.0.0.1:${port}/handshake`,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          vaultPath,
          executionMode: settings.executionMode,
          intentLlm: intentLlmConfig(app, settings)
        }),
        throw: false
      }), 1_500);
      if (response.status < 200 || response.status >= 300) continue;
      const payload = response.json as unknown;
      if (!isRecord(payload) || typeof payload.token !== "string") continue;
      return {
        port: String(port),
        token: payload.token,
        vaultRoot: typeof payload.vaultRoot === "string" ? payload.vaultRoot : vaultPath
      };
    } catch {
      // Try the next local port.
    }
  }
  throw new AgentError("没有发现可用的本地 Agent。请先启动 local-agent。");
}

function intentLlmConfig(app: App, settings: AgentSettings): Record<string, string> | undefined {
  if (!settings.model.trim()) return undefined;
  return {
    baseUrl: chatCompletionsUrl(settings.apiBaseUrl),
    model: settings.model.trim(),
    apiKey: app.secretStorage.getSecret(settings.secretId) ?? ""
  };
}

function localAgentPort(settings: AgentSettings): number | null {
  const port = Number(settings.localAgentPort);
  return Number.isInteger(port) && port > 0 && port <= 65535 ? port : null;
}

function candidatePorts(settings: AgentSettings): number[] {
  const result: number[] = [];
  const configured = localAgentPort(settings);
  if (configured) result.push(configured);
  for (let port = 8765; port <= 8785; port += 1) {
    if (!result.includes(port)) result.push(port);
  }
  return result;
}

function vaultBasePath(app: App): string {
  const adapter = app.vault.adapter as { getBasePath?: () => string };
  const path = adapter.getBasePath?.();
  if (!path) throw new AgentError("当前平台无法读取 Vault 根目录，请手动配置 local-agent。");
  return path;
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_resolve, reject) => {
      setTimeout(() => reject(new Error("timeout")), ms);
    })
  ]);
}

export function toAgentAnswer(payload: unknown): AgentAnswer {
  const output = unwrapToolOutput(payload);
  const trace = agentTrace(payload);
  if (isRecord(output) && isRecord(output.plan)) {
    return {
      answer: typeof output.message === "string" ? output.message : "已生成操作计划，等待确认。",
      citations: [],
      trace,
      operationPlan: localPlanFromPayload(output.plan, trace)
    };
  }
  if (isRecord(output) && Array.isArray(output.tasks)) {
    return withTrace(tasksAnswer(output.tasks.filter(isLocalTask)), trace);
  }
  if (isRecord(output) && typeof output.message === "string") {
    const citations = Array.isArray(output.citations)
      ? uniqueCitations(output.citations.filter(isLocalCitation))
      : [];
    return { answer: output.message, citations, trace };
  }
  if (isRecord(output) && Array.isArray(output.results)) {
    return withTrace(searchAnswer(output.results.filter(isLocalSearchResult)), trace);
  }
  if (isRecord(payload) && typeof payload.assistant_message === "string") {
    return { answer: payload.assistant_message, citations: [], trace };
  }
  throw new AgentError("本地 Agent 返回了无法识别的响应。");
}

function withTrace(answer: AgentAnswer, trace: AgentTraceStep[]): AgentAnswer {
  return trace.length ? { ...answer, trace } : answer;
}

function agentTrace(payload: unknown): AgentTraceStep[] {
  if (!isRecord(payload) || !Array.isArray(payload.trace)) return [];
  return payload.trace.filter(isTraceStep).map((step) => ({
    round: Number(step.round),
    toolName: step.toolName ?? step.tool_name ?? "",
    status: step.status,
    summary: step.summary,
    detail: isRecord(step.detail) ? step.detail : undefined
  }));
}

function unwrapToolOutput(payload: unknown): unknown {
  if (!isRecord(payload) || !isRecord(payload.execution)) return payload;
  const steps = payload.execution.step_results;
  if (!Array.isArray(steps) || !steps.length) return payload;
  const last = steps[steps.length - 1];
  if (!isRecord(last)) return payload;
  const stepOutput = last.output;
  if (isRecord(stepOutput) && "output" in stepOutput) return stepOutput.output;
  return stepOutput;
}

function tasksAnswer(tasks: LocalTask[]): AgentAnswer {
  if (!tasks.length) return { answer: "没有找到任务。", citations: [] };
  const visible = tasks.slice(0, 30);
  return {
    answer: [
      `找到 ${visible.length} 条任务：`,
      "",
      ...visible.map((task) => `- ${task.completed ? "[x]" : "[ ]"} ${task.title}（${task.path}:${task.line}）`)
    ].join("\n"),
    citations: uniqueCitations(visible.map((task) => ({ path: task.path, heading: task.heading })))
  };
}

function searchAnswer(results: LocalSearchResult[]): AgentAnswer {
  if (!results.length) return { answer: "没有找到相关笔记。", citations: [] };
  const visible = results.slice(0, 10);
  return {
    answer: [
      `找到 ${visible.length} 篇相关笔记：`,
      "",
      ...visible.map((item) => `- ${item.title ?? item.path}（${item.path}）${item.excerpt ? `\n  ${item.excerpt}` : ""}`)
    ].join("\n"),
    citations: uniqueCitations(visible.map((item) => ({ path: item.path })))
  };
}

function uniqueCitations(citations: AgentAnswer["citations"]): AgentAnswer["citations"] {
  const result: AgentAnswer["citations"] = [];
  for (const citation of citations) {
    if (!result.some((item) => item.path === citation.path && item.heading === citation.heading)) {
      result.push(citation);
    }
  }
  return result;
}

async function localAgentRequest(
  settings: AgentSettings,
  path: string,
  body: Record<string, unknown>
): Promise<unknown> {
  const port = localAgentPort(settings);
  if (!port) throw new AgentError("本地 Agent 端口未配置。");
  if (!settings.localAgentToken) throw new AgentError("本地 Agent 尚未配对。");
  const response = await requestUrl({
    url: `http://127.0.0.1:${port}${path}`,
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Agent-Token": settings.localAgentToken
    },
    body: JSON.stringify(body),
    throw: false
  });
  if (response.status < 200 || response.status >= 300) {
    const payload = response.json as unknown;
    const detail = isRecord(payload) && typeof payload.error === "string"
      ? `：${payload.error}`
      : "";
    throw new AgentError(`本地 Agent 操作失败（HTTP ${response.status}）${detail}`);
  }
  return response.json as unknown;
}

function localOperationResultText(value: unknown): string {
  if (!isRecord(value) || !isRecord(value.operation)) return "操作已执行。";
  const operation = value.operation;
  const path = typeof operation.path === "string" ? `：${operation.path}` : "";
  return `已执行 ${String(operation.type ?? "operation")}${path}`;
}

function isRisk(value: unknown): value is OperationPlan["risk"] {
  return value === "low" || value === "medium" || value === "high";
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function isStringMap(value: unknown): value is Record<string, string> {
  return isRecord(value) && Object.values(value).every((item) => typeof item === "string");
}

function isLocalTask(value: unknown): value is LocalTask {
  return isRecord(value) &&
    typeof value.path === "string" &&
    typeof value.line === "number" &&
    typeof value.title === "string" &&
    typeof value.completed === "boolean";
}

function isLocalSearchResult(value: unknown): value is LocalSearchResult {
  return isRecord(value) && typeof value.path === "string";
}

function isLocalCitation(value: unknown): value is AgentAnswer["citations"][number] {
  return isRecord(value) &&
    typeof value.path === "string" &&
    (value.heading === undefined || typeof value.heading === "string");
}

function isTraceStep(value: unknown): value is {
  round: number;
  toolName?: string;
  tool_name?: string;
  status: string;
  summary: string;
  detail?: unknown;
} {
  return isRecord(value) &&
    typeof value.round === "number" &&
    (typeof value.toolName === "string" || typeof value.tool_name === "string") &&
    typeof value.status === "string" &&
    typeof value.summary === "string";
}

function isLocalAgentTool(value: unknown): value is LocalAgentTool {
  return isRecord(value) &&
    typeof value.name === "string" &&
    typeof value.description === "string";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
