import assert from "node:assert/strict";
import test from "node:test";
import { TFile } from "obsidian";
import { answerWithTasks, parseMarkdownTasks } from "../src/features/task-actions/list-tasks";

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

test("今天未完成任务只返回今天到期且未完成的任务", async () => {
  const today = localDate(0);
  const tomorrow = localDate(1);
  const file = new TFile("Tasks.md");
  const app = {
    workspace: { getActiveFile: () => file },
    vault: {
      getMarkdownFiles: () => [file],
      cachedRead: async () => [
        `- [ ] 今天要做 📅 ${today}`,
        `- [ ] 明天再做 📅 ${tomorrow}`,
        `- [x] 今天已做 📅 ${today}`
      ].join("\n")
    }
  };

  const answer = await answerWithTasks(app as never, "我今天还有哪些任务没有完成", "vault");

  assert.match(answer.answer, /今天要做/);
  assert.doesNotMatch(answer.answer, /明天再做/);
  assert.doesNotMatch(answer.answer, /今天已做/);
});

function localDate(offsetDays: number): string {
  const date = new Date();
  date.setDate(date.getDate() + offsetDays);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}
