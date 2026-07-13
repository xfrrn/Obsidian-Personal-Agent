import assert from "node:assert/strict";
import test from "node:test";
import { TFile } from "obsidian";
import { askAgent } from "../src/features/assistant/agent-loop";
import { collectTasks, parseMarkdownTasks } from "../src/features/task-actions/list-tasks";

test("解析 Markdown 任务并保留标题位置", () => {
  const tasks = parseMarkdownTasks("Projects/A.md", [
    "# 项目 A",
    "- [ ] 写 Agent tool router",
    "- [x] 完成检索优化",
    "+ [ ] 补充 CommonMark 任务",
    "普通段落"
  ].join("\n"));

  assert.deepEqual(tasks, [
    {
      path: "Projects/A.md",
      line: 2,
      title: "写 Agent tool router",
      completed: false,
      heading: "项目 A"
    },
    {
      path: "Projects/A.md",
      line: 3,
      title: "完成检索优化",
      completed: true,
      heading: "项目 A"
    },
    {
      path: "Projects/A.md",
      line: 4,
      title: "补充 CommonMark 任务",
      completed: false,
      heading: "项目 A"
    }
  ]);
});

test("没有 local-agent 时拒绝任务查询", async () => {
  await assert.rejects(
    () => askAgent({} as never, { localAgentToken: "" } as never, "有什么任务", "vault"),
    /local-agent/
  );
});

test("收集 Markdown 任务供 local-agent 使用", async () => {
  const file = new TFile("Tasks.md");
  const app = {
    workspace: { getActiveFile: () => file },
    vault: {
      getMarkdownFiles: () => [file],
      cachedRead: async () => [
        "- [ ] 今天要做 📅 2026-07-13",
        "- [x] 今天已做 📅 2026-07-13"
      ].join("\n")
    }
  };

  const tasks = await collectTasks(app as never, "vault");

  assert.deepEqual(tasks.map((task) => [task.title, task.completed, task.dueDate]), [
    ["今天要做 📅 2026-07-13", false, "2026-07-13"],
    ["今天已做 📅 2026-07-13", true, "2026-07-13"]
  ]);
});

test("优先使用 Tasks 插件获取任务", async () => {
  const today = localDate(0);
  const file = new TFile("Tasks.md");
  const app = {
    workspace: { getActiveFile: () => file },
    plugins: {
      plugins: {
        "obsidian-tasks-plugin": {
          apiV1: {
            executeToggleTaskDoneCommand: (line: string, path: string) => `${line} ✅ ${path}`
          },
          getTasks: () => [
            {
              path: "Tasks.md",
              lineNumber: 4,
              description: "插件任务",
              originalMarkdown: "- [ ] 插件任务 📅 2026-07-13",
              isDone: false,
              dueDate: { format: () => today },
              heading: "插件标题"
            }
          ]
        }
      }
    },
    vault: {
      getMarkdownFiles: () => [file],
      cachedRead: async () => { throw new Error("should use Tasks plugin"); }
    }
  };

  const tasks = await collectTasks(app as never, "vault");

  assert.deepEqual(tasks, [{
    path: "Tasks.md",
    line: 5,
    title: "插件任务",
    completed: false,
    dueDate: today,
    heading: "插件标题",
    lineText: "- [ ] 插件任务 📅 2026-07-13",
    completedLineText: "- [ ] 插件任务 📅 2026-07-13 ✅ Tasks.md"
  }]);
});

function localDate(offsetDays: number): string {
  const date = new Date();
  date.setDate(date.getDate() + offsetDays);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}
