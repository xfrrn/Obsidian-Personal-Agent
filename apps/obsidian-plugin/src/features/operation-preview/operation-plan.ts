import { AgentError, parseJsonObject } from "../../utils/protocol";

export type OperationRisk = "low" | "medium" | "high";

export type MetadataValue = string | number | boolean | string[];

export type KnowledgeOperation =
  | { type: "create-note"; path: string; content: string }
  | { type: "update-note"; path: string; oldText: string; newText: string }
  | { type: "move-note"; path: string; targetPath: string }
  | {
    type: "update-metadata";
    path: string;
    set?: Record<string, MetadataValue>;
    remove?: string[];
    addTags?: string[];
    removeTags?: string[];
  }
  | { type: "create-task"; path: string; title: string }
  | { type: "invoke-plugin"; commandId: string };

export interface OperationPlan {
  planId?: string;
  createdAt?: string;
  expectedHashes?: Record<string, string>;
  expiresAt?: string;
  integrityHash?: string;
  requiresConfirmation?: boolean;
  confirmationToken?: string;
  rollbackToken?: string;
  status?: string;
  managedBy?: "local-agent";
  summary: string;
  risk: OperationRisk;
  operations: KnowledgeOperation[];
}

const ALLOWED_PLUGIN_COMMANDS = new Set(["workspace:save-file"]);

export function parseOperationPlan(
  text: string,
  existingPaths: ReadonlySet<string>,
  sourcePaths: ReadonlySet<string>
): OperationPlan {
  const value = parseJsonObject(text);
  if (!Array.isArray(value.operations)) {
    throw new AgentError("模型没有按要求返回修改操作列表。");
  }
  if (value.operations.length > 10) {
    throw new AgentError("第一版最多一次执行 10 个操作，请拆成更小的请求。");
  }

  const operations = value.operations.map((raw) =>
    parseOperation(raw, existingPaths, sourcePaths)
  );
  if (!operations.length) throw new AgentError("模型没有生成可执行的修改操作。");
  const pluginIndex = operations.findIndex((operation) => operation.type === "invoke-plugin");
  if (pluginIndex >= 0 && pluginIndex !== operations.length - 1) {
    throw new AgentError("插件调用必须放在计划最后一步。");
  }

  return {
    summary: typeof value.summary === "string" && value.summary.trim()
      ? value.summary.trim()
      : "准备修改知识库",
    risk: riskOf(operations),
    operations
  };
}

export function describeOperation(operation: KnowledgeOperation): string {
  if (operation.type === "create-note") return `创建笔记：${operation.path}`;
  if (operation.type === "update-note") return `精确替换：${operation.path}`;
  if (operation.type === "move-note") return `移动笔记：${operation.path} -> ${operation.targetPath}`;
  if (operation.type === "update-metadata") return `更新元数据：${operation.path}`;
  if (operation.type === "create-task") return `追加任务：${operation.path} - ${operation.title}`;
  return `调用插件命令：${operation.commandId}`;
}

function parseOperation(
  raw: unknown,
  existingPaths: ReadonlySet<string>,
  sourcePaths: ReadonlySet<string>
): KnowledgeOperation {
  if (!isRecord(raw) || typeof raw.type !== "string") {
    throw new AgentError("模型返回了无法识别的修改操作。");
  }

  if (raw.type === "create-note") {
    const path = safeMarkdownPath(raw.path);
    if (existingPaths.has(path)) throw new AgentError(`计划要创建的笔记已存在：${path}`);
    return { type: "create-note", path, content: requiredString(raw.content, "新笔记内容") };
  }

  if (raw.type === "update-note") {
    const path = existingSourcePath(raw.path, existingPaths, sourcePaths);
    return {
      type: "update-note",
      path,
      oldText: requiredString(raw.oldText, "原文"),
      newText: typeof raw.newText === "string" ? raw.newText : ""
    };
  }

  if (raw.type === "move-note") {
    const path = existingSourcePath(raw.path, existingPaths, sourcePaths);
    const targetPath = safeMarkdownPath(raw.targetPath);
    if (existingPaths.has(targetPath)) throw new AgentError(`移动目标已存在：${targetPath}`);
    return { type: "move-note", path, targetPath };
  }

  if (raw.type === "update-metadata") {
    return {
      type: "update-metadata",
      path: existingSourcePath(raw.path, existingPaths, sourcePaths),
      set: optionalMetadataMap(raw.set),
      remove: optionalKeyList(raw.remove),
      addTags: optionalTagList(raw.addTags),
      removeTags: optionalTagList(raw.removeTags)
    };
  }

  if (raw.type === "create-task") {
    return {
      type: "create-task",
      path: existingSourcePath(raw.path, existingPaths, sourcePaths),
      title: requiredSingleLine(raw.title, "任务标题")
    };
  }

  if (raw.type === "invoke-plugin") {
    const commandId = requiredSingleLine(raw.commandId, "插件命令 ID");
    if (!ALLOWED_PLUGIN_COMMANDS.has(commandId)) {
      throw new AgentError(`不允许调用插件命令：${commandId}`);
    }
    return { type: "invoke-plugin", commandId };
  }

  throw new AgentError(`第一版不支持操作类型：${raw.type}`);
}

function riskOf(operations: KnowledgeOperation[]): OperationRisk {
  if (operations.some((operation) => operation.type === "invoke-plugin")) return "high";
  if (operations.some((operation) => operation.type === "move-note")) return "medium";
  return "low";
}

function existingSourcePath(
  value: unknown,
  existingPaths: ReadonlySet<string>,
  sourcePaths: ReadonlySet<string>
): string {
  const path = safeMarkdownPath(value);
  if (!existingPaths.has(path)) throw new AgentError(`笔记不存在：${path}`);
  if (!sourcePaths.has(path)) throw new AgentError(`计划只能修改本次上下文中的笔记：${path}`);
  return path;
}

function safeMarkdownPath(value: unknown): string {
  if (typeof value !== "string") throw new AgentError("修改操作缺少笔记路径。");
  const raw = value.trim().replace(/\\/g, "/");
  if (!raw || raw.startsWith("/") || raw.includes("://") || /^[a-zA-Z]:/.test(raw)) {
    throw new AgentError(`不安全的笔记路径：${value}`);
  }

  const path = raw.split("/").filter((part) => part && part !== ".").join("/");
  const parts = path.split("/");
  if (!path.endsWith(".md") || parts.includes("..") || parts.some((part) => part === ".obsidian")) {
    throw new AgentError(`不安全的笔记路径：${value}`);
  }
  return path;
}

function optionalMetadataMap(value: unknown): Record<string, MetadataValue> | undefined {
  if (value === undefined) return undefined;
  if (!isRecord(value)) throw new AgentError("元数据 set 必须是对象。");
  const result: Record<string, MetadataValue> = {};
  for (const [key, item] of Object.entries(value)) {
    result[metadataKey(key)] = metadataValue(item);
  }
  return result;
}

function optionalKeyList(value: unknown): string[] | undefined {
  if (value === undefined) return undefined;
  if (!Array.isArray(value)) throw new AgentError("元数据 remove 必须是数组。");
  return value.map(metadataKey);
}

function optionalTagList(value: unknown): string[] | undefined {
  if (value === undefined) return undefined;
  if (!Array.isArray(value)) throw new AgentError("标签列表必须是数组。");
  return value.map((item) => requiredSingleLine(item, "标签")).filter(unique);
}

function metadataKey(value: unknown): string {
  const key = requiredString(value, "元数据字段");
  if (key === "__proto__" || key.includes("\n") || key.includes(":")) {
    throw new AgentError(`不安全的元数据字段：${key}`);
  }
  return key;
}

function metadataValue(value: unknown): MetadataValue {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) {
    return value;
  }
  throw new AgentError("第一版元数据值只支持字符串、数字、布尔值和字符串数组。");
}

function requiredString(value: unknown, name: string): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new AgentError(`修改操作缺少${name}。`);
  }
  return value.trim();
}

function requiredSingleLine(value: unknown, name: string): string {
  const result = requiredString(value, name);
  if (/[\r\n\u0000-\u001f\u007f]/.test(result)) {
    throw new AgentError(`${name}不能包含换行或控制字符。`);
  }
  return result;
}

function unique(value: string, index: number, array: string[]): boolean {
  return array.indexOf(value) === index;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
