import assert from "node:assert/strict";
import test from "node:test";
import { parseMarkdownTasks } from "../src/features/task-actions/list-tasks";

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
