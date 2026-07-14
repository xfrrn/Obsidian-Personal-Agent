import {
  App,
  normalizePath,
  TFile,
  TFolder
} from "obsidian";
import { callModel } from "../../api/model-client";
import { buildLocalOperationPlan, stageLocalOperationPlan } from "../../api/local-agent-client";
import type { QueryScope } from "../assistant/types";
import {
  PLAN_GENERATION_PROMPT,
  PLAN_NOTE_SELECTION_PROMPT
} from "../assistant/prompts";
import { AgentError, isTaskCompletionRequest } from "../../utils/protocol";
import type { AgentSettings } from "../../settings/settings";
import {
  extractFileReferencePaths,
  getCurrentSource,
  loadSources,
  SourceDocument
} from "../../obsidian/vault-reader";
import { selectCandidateNotePaths } from "../knowledge-search/search-notes";
import {
  describeOperation,
  KnowledgeOperation,
  OperationPlan,
  parseOperationPlan
} from "./operation-plan";

export {
  describeOperation,
  KnowledgeOperation,
  OperationPlan,
  parseOperationPlan
} from "./operation-plan";

const AUDIT_DIR = ".obsidian-agent-data";
const AUDIT_PATH = `${AUDIT_DIR}/audit.jsonl`;
let executing = false;

export async function buildOperationPlan(
  app: App,
  settings: AgentSettings,
  request: string,
  scope: QueryScope
): Promise<OperationPlan> {
  const cleanRequest = request.trim();
  if (!cleanRequest) throw new AgentError("请输入要执行的修改请求。");
  if (isTaskCompletionRequest(cleanRequest)) {
    if (settings.localAgentToken) return buildLocalOperationPlan(app, settings, cleanRequest, scope);
    throw new AgentError("任务修改需要先配对并启动 local-agent。");
  }

  const sources = await getPlanningSources(app, settings, cleanRequest, scope);
  const existingPaths = new Set(app.vault.getMarkdownFiles().map((file) => file.path));
  const sourcePaths = new Set(sources.map((source) => source.path));
  const response = await callModel(app, settings, [
    {
      role: "system",
      content: PLAN_GENERATION_PROMPT
    },
    {
      role: "user",
      content: `用户请求：${cleanRequest}\n\n可用笔记：\n${JSON.stringify(sources)}`
    }
  ]);

  const plan = parseOperationPlan(response, existingPaths, sourcePaths);
  assertPlanMatchesSources(plan, sources);
  const versionedPlan: OperationPlan = {
    ...plan,
    planId: crypto.randomUUID(),
    createdAt: new Date().toISOString(),
    expectedHashes: await hashPaths(app, operationSourcePaths(plan))
  };
  if (settings.localAgentToken && !plan.operations.some((operation) => operation.type === "invoke-plugin")) {
    return stageLocalOperationPlan(settings, versionedPlan, [...sourcePaths]);
  }
  return versionedPlan;
}

export async function executeOperationPlan(
  app: App,
  plan: OperationPlan
): Promise<string[]> {
  if (executing) throw new AgentError("已有修改计划正在执行，请稍后再试。");
  executing = true;
  const results: string[] = [];
  const rollback: Array<() => Promise<void>> = [];

  try {
    const beforeHashes = await assertExpectedHashes(app, plan);
    await appendAudit(app, "started", plan, undefined, beforeHashes);
    for (const operation of plan.operations) {
      await executeOperation(app, operation, rollback);
      results.push(`已执行：${describeOperation(operation)}`);
    }
    await appendAudit(app, "succeeded", plan, undefined, await currentHashes(app, affectedPaths(plan)));
    return results;
  } catch (error) {
    const message = error instanceof Error ? error.message : "未知错误";
    try {
      await rollbackDone(rollback);
      await appendAudit(app, "rolled-back", plan, message, await currentHashes(app, affectedPaths(plan)));
    } catch (rollbackError) {
      const rollbackMessage = rollbackError instanceof Error ? rollbackError.message : "未知错误";
      await appendAudit(app, "rollback-failed", plan, `${message}; ${rollbackMessage}`);
      throw new AgentError(`执行失败，且回滚失败：${rollbackMessage}`);
    }
    throw error instanceof AgentError
      ? error
      : new AgentError(`执行失败，已回滚：${message}`);
  } finally {
    executing = false;
  }
}

async function executeOperation(
  app: App,
  operation: KnowledgeOperation,
  rollback: Array<() => Promise<void>>
): Promise<void> {
  if (operation.type === "create-note") {
    if (app.vault.getAbstractFileByPath(operation.path)) {
      throw new AgentError(`笔记已存在，已停止执行：${operation.path}`);
    }
    await ensureParentFolder(app, operation.path);
    await app.vault.create(operation.path, operation.content);
    const createdHash = await sha256(operation.content);
    rollback.push(async () => {
      const file = getMarkdownFile(app, operation.path);
      await assertRollbackHash(app, file, createdHash);
      await app.vault.delete(file);
    });
    return;
  }

  if (operation.type === "move-note") {
    const file = getMarkdownFile(app, operation.path);
    const movedHash = await sha256(await app.vault.cachedRead(file));
    if (app.vault.getAbstractFileByPath(operation.targetPath)) {
      throw new AgentError(`目标路径已存在，已停止执行：${operation.targetPath}`);
    }
    await ensureParentFolder(app, operation.targetPath);
    await app.fileManager.renameFile(file, operation.targetPath);
    rollback.push(async () => {
      const moved = getMarkdownFile(app, operation.targetPath);
      await assertRollbackHash(app, moved, movedHash);
      await app.fileManager.renameFile(moved, operation.path);
    });
    return;
  }

  if (operation.type === "invoke-plugin") {
    const commands = (app as App & {
      commands?: { executeCommandById(commandId: string): boolean };
    }).commands;
    if (!commands?.executeCommandById(operation.commandId)) {
      throw new AgentError(`插件命令不存在或执行失败：${operation.commandId}`);
    }
    return;
  }

  const file = getMarkdownFile(app, operation.path);
  const content = await app.vault.cachedRead(file);
  let writtenHash: string;

  if (operation.type === "update-note") {
    assertUniqueText(content, operation.oldText, operation.path);
    const updated = content.replace(operation.oldText, operation.newText);
    await app.vault.modify(file, updated);
    writtenHash = await sha256(updated);
  } else if (operation.type === "update-metadata") {
    await app.fileManager.processFrontMatter(file, (frontmatter) => {
      applyMetadata(frontmatter, operation);
    });
    writtenHash = await sha256(await app.vault.cachedRead(file));
  } else {
    const updated = `${content.replace(/\s+$/, "")}\n\n- [ ] ${operation.title}\n`;
    await app.vault.modify(file, updated);
    writtenHash = await sha256(updated);
  }

  // ponytail: in-memory snapshot; persist snapshots if executions must survive app restart.
  rollback.push(async () => {
    const current = getMarkdownFile(app, operation.path);
    await assertRollbackHash(app, current, writtenHash);
    await app.vault.modify(current, content);
  });
}

async function assertRollbackHash(app: App, file: TFile, expectedHash: string): Promise<void> {
  if (await sha256(await app.vault.cachedRead(file)) !== expectedHash) {
    throw new AgentError(`回滚冲突，文件在执行期间被修改：${file.path}`);
  }
}

async function getPlanningSources(
  app: App,
  settings: AgentSettings,
  request: string,
  scope: QueryScope
): Promise<SourceDocument[]> {
  if (scope === "current") {
    const source = await getCurrentSource(app);
    const referenced = await loadSources(app, withoutPath(extractFileReferencePaths(app, request), source.path));
    return [source, ...referenced];
  }

  const paths = uniquePaths([
    ...extractFileReferencePaths(app, request),
    ...await selectCandidateNotePaths(
      app,
      settings,
      request,
      PLAN_NOTE_SELECTION_PROMPT,
      "修改请求"
    )
  ]);
  return loadSources(app, paths);
}

function uniquePaths(paths: readonly string[]): string[] {
  return [...new Set(paths)];
}

function withoutPath(paths: readonly string[], path: string): string[] {
  return paths.filter((item) => item !== path);
}

function assertPlanMatchesSources(
  plan: OperationPlan,
  sources: SourceDocument[]
): void {
  const contents = new Map(sources.map((source) => [source.path, source.content]));
  for (const operation of plan.operations) {
    if (operation.type !== "update-note") continue;
    const content = contents.get(operation.path) ?? "";
    assertUniqueText(content, operation.oldText, operation.path);
  }
}

function assertUniqueText(content: string, text: string, path: string): void {
  const first = content.indexOf(text);
  if (first < 0) throw new AgentError(`原文已变化，已停止执行：${path}`);
  if (content.indexOf(text, first + text.length) >= 0) {
    throw new AgentError(`原文在笔记中不唯一，已停止执行：${path}`);
  }
}

function applyMetadata(
  frontmatter: Record<string, unknown>,
  operation: Extract<KnowledgeOperation, { type: "update-metadata" }>
): void {
  for (const [key, value] of Object.entries(operation.set ?? {})) {
    frontmatter[key] = value;
  }
  for (const key of operation.remove ?? []) {
    delete frontmatter[key];
  }

  const tags = normalizeTags(frontmatter.tags);
  for (const tag of operation.addTags ?? []) {
    if (!tags.includes(tag)) tags.push(tag);
  }
  if (operation.removeTags?.length) {
    frontmatter.tags = tags.filter((tag) => !operation.removeTags?.includes(tag));
  } else if (operation.addTags?.length) {
    frontmatter.tags = tags;
  }
}

function normalizeTags(value: unknown): string[] {
  if (Array.isArray(value)) return value.filter((item): item is string => typeof item === "string");
  if (typeof value === "string" && value.trim()) return [value.trim()];
  return [];
}

async function rollbackDone(rollback: Array<() => Promise<void>>): Promise<void> {
  for (const undo of rollback.reverse()) {
    await undo();
  }
}

async function appendAudit(
  app: App,
  status: string,
  plan: OperationPlan,
  error?: string,
  actualHashes?: Record<string, string>
): Promise<void> {
  const adapter = app.vault.adapter;
  if (!(await adapter.exists(AUDIT_DIR))) await adapter.mkdir(AUDIT_DIR);
  const line = JSON.stringify({
    at: new Date().toISOString(),
    planId: plan.planId,
    createdAt: plan.createdAt,
    status,
    risk: plan.risk,
    summary: plan.summary,
    expectedHashes: plan.expectedHashes,
    actualHashes,
    operations: plan.operations,
    error
  });
  // ponytail: JSONL append via read+write; replace with adapter-level append if audit grows large.
  const old = await adapter.exists(AUDIT_PATH) ? await adapter.read(AUDIT_PATH) : "";
  await adapter.write(AUDIT_PATH, `${old}${line}\n`);
}

async function assertExpectedHashes(
  app: App,
  plan: OperationPlan
): Promise<Record<string, string>> {
  if (!plan.planId || !plan.createdAt || !plan.expectedHashes) {
    throw new AgentError("修改计划缺少版本信息，请重新生成。");
  }
  const actual = await hashPaths(app, operationSourcePaths(plan));
  for (const [path, hash] of Object.entries(actual)) {
    if (plan.expectedHashes[path] !== hash) {
      throw new AgentError(`笔记在预览后已变化，请重新生成计划：${path}`);
    }
  }
  return actual;
}

function operationSourcePaths(plan: OperationPlan): string[] {
  const paths = new Set<string>();
  for (const operation of plan.operations) {
    if (operation.type !== "create-note" && operation.type !== "invoke-plugin") paths.add(operation.path);
  }
  return [...paths];
}

function affectedPaths(plan: OperationPlan): string[] {
  const paths = new Set<string>();
  for (const operation of plan.operations) {
    if (operation.type === "invoke-plugin") continue;
    paths.add(operation.path);
    if (operation.type === "move-note") paths.add(operation.targetPath);
  }
  return [...paths];
}

async function hashPaths(app: App, paths: readonly string[]): Promise<Record<string, string>> {
  const result: Record<string, string> = {};
  for (const path of paths) {
    const file = getMarkdownFile(app, path);
    result[path] = await sha256(await app.vault.cachedRead(file));
  }
  return result;
}

async function currentHashes(app: App, paths: readonly string[]): Promise<Record<string, string>> {
  const result: Record<string, string> = {};
  for (const path of paths) {
    const file = app.vault.getAbstractFileByPath(normalizePath(path));
    if (file instanceof TFile && file.extension === "md") {
      result[path] = await sha256(await app.vault.cachedRead(file));
    }
  }
  return result;
}

async function sha256(content: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(content));
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
}

function getMarkdownFile(app: App, path: string): TFile {
  const file = app.vault.getAbstractFileByPath(normalizePath(path));
  if (!(file instanceof TFile) || file.extension !== "md") {
    throw new AgentError(`笔记不存在：${path}`);
  }
  return file;
}

async function ensureParentFolder(app: App, path: string): Promise<void> {
  const parts = normalizePath(path).split("/").slice(0, -1);
  let current = "";
  for (const part of parts) {
    current = current ? `${current}/${part}` : part;
    const existing = app.vault.getAbstractFileByPath(current);
    if (existing instanceof TFolder) continue;
    if (existing) throw new AgentError(`无法创建目录，路径已被文件占用：${current}`);
    await app.vault.createFolder(current);
  }
}
