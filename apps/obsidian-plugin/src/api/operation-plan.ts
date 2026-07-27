export type OperationRisk = "low" | "medium" | "high";

export type MetadataValue = string | number | boolean | string[];

export type KnowledgeOperation =
  | { type: "create-note"; path: string; content: string }
  | { type: "update-note"; path: string; oldText: string; newText: string }
  | { type: "move-note"; path: string; targetPath: string }
  | { type: "trash-note"; path: string; trashPath?: string }
  | { type: "create-folder"; path: string }
  | { type: "delete-folder"; path: string }
  | {
    type: "update-metadata";
    path: string;
    set?: Record<string, MetadataValue>;
    remove?: string[];
    addTags?: string[];
    removeTags?: string[];
  }
  | { type: "create-task"; path: string; title: string };

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

export function describeOperation(operation: KnowledgeOperation): string {
  if (operation.type === "create-note") return `创建笔记：${operation.path}`;
  if (operation.type === "update-note") return `精确替换：${operation.path}`;
  if (operation.type === "move-note") return `移动笔记：${operation.path} → ${operation.targetPath}`;
  if (operation.type === "trash-note") return `移入废纸篓：${operation.path}`;
  if (operation.type === "create-folder") return `创建目录：${operation.path}`;
  if (operation.type === "delete-folder") return `删除空目录：${operation.path}`;
  if (operation.type === "update-metadata") return `更新元数据：${operation.path}`;
  return `追加任务：${operation.path} - ${operation.title}`;
}
