import assert from "node:assert/strict";
import test from "node:test";
import { AgentError } from "../src/protocol";
import { parseOperationPlan } from "../src/operation-plan";

test("修改计划支持第一版六种操作并计算风险", () => {
  const plan = parseOperationPlan(
    JSON.stringify({
      summary: "整理项目笔记",
      operations: [
        { type: "create-note", path: "Projects/New.md", content: "# New" },
        { type: "update-note", path: "A.md", oldText: "old", newText: "new" },
        { type: "move-note", path: "A.md", targetPath: "Archive/A.md" },
        { type: "update-metadata", path: "A.md", set: { status: "done" }, addTags: ["x"] },
        { type: "create-task", path: "A.md", title: "收尾" },
        { type: "invoke-plugin", commandId: "workspace:save-file" }
      ]
    }),
    new Set(["A.md"]),
    new Set(["A.md"])
  );

  assert.equal(plan.risk, "high");
  assert.equal(plan.operations.length, 6);
});

test("修改计划拒绝越权路径、非上下文笔记和危险元数据", () => {
  assert.throws(
    () => parseOperationPlan(
      JSON.stringify({ operations: [{ type: "create-note", path: "../x.md", content: "x" }] }),
      new Set(),
      new Set()
    ),
    AgentError
  );

  assert.throws(
    () => parseOperationPlan(
      JSON.stringify({ operations: [{ type: "update-note", path: "B.md", oldText: "x", newText: "y" }] }),
      new Set(["B.md"]),
      new Set(["A.md"])
    ),
    AgentError
  );

  assert.throws(
    () => parseOperationPlan(
      JSON.stringify({ operations: [{ type: "update-metadata", path: "A.md", set: { "bad:key": "x" } }] }),
      new Set(["A.md"]),
      new Set(["A.md"])
    ),
    AgentError
  );
});

test("第一版拒绝删除、超额操作和非末尾插件调用", () => {
  assert.throws(
    () => parseOperationPlan(
      JSON.stringify({ operations: [{ type: "delete-note", path: "A.md" }] }),
      new Set(["A.md"]),
      new Set(["A.md"])
    ),
    AgentError
  );

  assert.throws(
    () => parseOperationPlan(
      JSON.stringify({
        operations: Array.from({ length: 11 }, (_, index) => ({
          type: "create-note",
          path: `${index}.md`,
          content: "x"
        }))
      }),
      new Set(),
      new Set()
    ),
    AgentError
  );

  assert.throws(
    () => parseOperationPlan(
      JSON.stringify({
        operations: [
          { type: "invoke-plugin", commandId: "x" },
          { type: "create-task", path: "A.md", title: "t" }
        ]
      }),
      new Set(["A.md"]),
      new Set(["A.md"])
    ),
    AgentError
  );
});
