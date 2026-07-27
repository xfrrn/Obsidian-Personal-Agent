import assert from "node:assert/strict";
import test from "node:test";
import { parseSseFrames, toOperationPlan } from "../src/api/local-agent-client";

test("SSE parser preserves partial frames and emits complete events", () => {
  const first = parseSseFrames(
    'data: {"kind":"turn_started","text":"","data":{"submission_id":1}}\n\n' +
    'data: {"kind":"assistant_message"'
  );
  assert.equal(first.events.length, 1);
  assert.equal(first.events[0].kind, "turn_started");
  assert.match(first.pending, /assistant_message/);

  const second = parseSseFrames(
    first.pending + ',"text":"你好","data":{"delta":true}}\n\n'
  );
  assert.equal(second.events.length, 1);
  assert.equal(second.events[0].text, "你好");
  assert.equal(second.pending, "");
});

test("SSE parser accepts a final frame without a trailing boundary", () => {
  const result = parseSseFrames('data: {"kind":"turn_finished","text":"","data":{}}', true);
  assert.deepEqual(result.events.map((event) => event.kind), ["turn_finished"]);
});

test("operation_plan event payload becomes a managed plan", () => {
  const plan = toOperationPlan({
    planId: "op_1",
    summary: "移动笔记",
    risk: "medium",
    requiresConfirmation: true,
    confirmationToken: "once",
    operations: [{ type: "move-note", path: "A.md", targetPath: "Archive/A.md" }]
  });
  assert.equal(plan.planId, "op_1");
  assert.equal(plan.managedBy, "local-agent");
  assert.equal(plan.operations.length, 1);
});
