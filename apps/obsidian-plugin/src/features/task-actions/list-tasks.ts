import type { App } from "obsidian";
import type { QueryScope } from "../assistant/types";

export interface MarkdownTask {
  path: string;
  line: number;
  title: string;
  completed: boolean;
  heading?: string;
  dueDate?: string;
}

type TasksPlugin = { getTasks: () => unknown };

export function parseMarkdownTasks(path: string, content: string): MarkdownTask[] {
  const tasks: MarkdownTask[] = [];
  let heading: string | undefined;

  content.split(/\r?\n/).forEach((line, index) => {
    const headingMatch = /^(#{1,6})\s+(.+?)\s*$/.exec(line);
    if (headingMatch) heading = headingMatch[2];

    const taskMatch = /^\s*[-+*]\s+\[([ xX])\]\s+(.+?)\s*$/.exec(line);
    if (!taskMatch) return;
    const due = dueDate(taskMatch[2]);
    tasks.push({
      path,
      line: index + 1,
      title: taskMatch[2],
      completed: taskMatch[1].toLowerCase() === "x",
      heading,
      ...(due ? { dueDate: due } : {})
    });
  });

  return tasks;
}

function dueDate(title: string): string | undefined {
  return /📅\s*(\d{4}-\d{2}-\d{2})/.exec(title)?.[1];
}

export async function collectTasks(app: App, scope: QueryScope): Promise<MarkdownTask[]> {
  const pluginTasks = collectTasksFromPlugin(app, scope);
  if (pluginTasks) return pluginTasks;

  const activeFile = app.workspace.getActiveFile();
  const files = scope === "current"
    ? activeFile ? [activeFile] : []
    : app.vault.getMarkdownFiles();
  const all: MarkdownTask[] = [];

  for (const file of files) {
    const content = await app.vault.cachedRead(file);
    all.push(...parseMarkdownTasks(file.path, content));
  }
  return all;
}

export function collectTasksFromPlugin(app: App, scope: QueryScope): MarkdownTask[] | null {
  const plugin = tasksPlugin(app);
  if (!plugin) return null;

  const activePath = scope === "current" ? app.workspace.getActiveFile()?.path : undefined;
  const rawTasks = plugin.getTasks();
  if (!Array.isArray(rawTasks)) return null;

  return rawTasks
    .map(taskFromPlugin)
    .filter((task): task is MarkdownTask => !!task)
    .filter((task) => !activePath || task.path === activePath);
}

function tasksPlugin(app: App): TasksPlugin | null {
  const plugins = (app as unknown as { plugins?: { plugins?: Record<string, unknown> } }).plugins?.plugins;
  const plugin = plugins?.["obsidian-tasks-plugin"];
  return isRecord(plugin) && typeof plugin.getTasks === "function"
    ? { getTasks: plugin.getTasks.bind(plugin) as () => unknown }
    : null;
}

function taskFromPlugin(value: unknown): MarkdownTask | null {
  if (!isRecord(value)) return null;
  const location = isRecord(value.taskLocation) ? value.taskLocation : {};
  const path = stringValue(value.path) ?? stringValue(location.path);
  const lineNumber = numberValue(value.lineNumber) ?? numberValue(location.lineNumber);
  const title = stringValue(value.description) ?? titleFromMarkdown(stringValue(value.originalMarkdown));
  const completed = completedValue(value);
  if (!path || lineNumber === undefined || !title || completed === undefined) return null;

  const heading = stringValue(value.heading) ?? stringValue(value.precedingHeader) ?? stringValue(location.precedingHeader);
  const due = isoDate(value.dueDate);
  return {
    path,
    line: lineNumber + 1,
    title,
    completed,
    ...(heading ? { heading } : {}),
    ...(due ? { dueDate: due } : {})
  };
}

function completedValue(task: Record<string, unknown>): boolean | undefined {
  if (typeof task.isDone === "boolean") return task.isDone;
  const status = isRecord(task.status) ? task.status : {};
  if (typeof status.isCompleted === "function") {
    const value = status.isCompleted();
    return typeof value === "boolean" ? value : undefined;
  }
  return undefined;
}

function titleFromMarkdown(value: string | undefined): string | undefined {
  return value?.match(/^\s*[-+*]\s+\[[^\]]\]\s+(.+?)\s*$/)?.[1];
}

function isoDate(value: unknown): string | undefined {
  if (typeof value === "string") return value.match(/^\d{4}-\d{2}-\d{2}$/)?.[0];
  if (isRecord(value) && typeof value.format === "function") {
    const formatted = value.format("YYYY-MM-DD");
    return typeof formatted === "string" ? formatted : undefined;
  }
  return undefined;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value ? value : undefined;
}

function numberValue(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
