import type { App } from "obsidian";
import type { AgentAnswer, AgentCitation } from "../../utils/protocol";
import type { QueryScope } from "../assistant/types";

export interface MarkdownTask {
  path: string;
  line: number;
  title: string;
  completed: boolean;
  heading?: string;
  dueDate?: string;
}

export async function answerWithTasks(
  app: App,
  question: string,
  scope: QueryScope
): Promise<AgentAnswer> {
  const tasks = await collectTasks(app, scope);
  const wantDone = /已完成|完成了|done|completed/i.test(question);
  const dueOn = question.includes("今天") ? todayIso() : "";
  const visible = tasks
    .filter((task) => task.completed === wantDone)
    .filter((task) => !dueOn || task.dueDate === dueOn)
    .slice(0, 30);
  const label = `${dueOn ? "今天的" : ""}${wantDone ? "已完成任务" : "未完成任务"}`;

  if (!visible.length) {
    return { answer: `没有找到${label}。`, citations: [] };
  }

  const answer = [
    `找到 ${visible.length} 条${label}：`,
    "",
    ...visible.map((task) => `- ${task.title}（${task.path}:${task.line}）`)
  ].join("\n");
  return { answer, citations: uniqueCitations(visible) };
}

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

function todayIso(): string {
  const date = new Date();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

async function collectTasks(app: App, scope: QueryScope): Promise<MarkdownTask[]> {
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

function uniqueCitations(tasks: MarkdownTask[]): AgentCitation[] {
  const citations: AgentCitation[] = [];
  for (const task of tasks) {
    const citation = { path: task.path, heading: task.heading };
    if (!citations.some((item) => item.path === citation.path && item.heading === citation.heading)) {
      citations.push(citation);
    }
  }
  return citations;
}
