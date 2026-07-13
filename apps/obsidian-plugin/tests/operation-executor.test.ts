import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import test from "node:test";
import type { App } from "obsidian";
import { TFile } from "obsidian";
import {
  buildOperationPlan,
  executeOperationPlan,
  type OperationPlan
} from "../src/features/operation-preview/operation-executor";

test("执行成功会写入内容和审计记录", async () => {
  const app = new FakeApp({ "A.md": "old" });
  const plan = await operationPlan(app, [
    { type: "update-note", path: "A.md", oldText: "old", newText: "new" }
  ]);

  await executeOperationPlan(app as unknown as App, plan);

  assert.equal(app.contents.get("A.md"), "new");
  assert.match(app.audit, /"status":"succeeded"/);
});

test("后续步骤失败会回滚已完成的修改", async () => {
  const app = new FakeApp({ "A.md": "old A", "B.md": "old B" });
  const plan = await operationPlan(app, [
    { type: "update-note", path: "A.md", oldText: "old A", newText: "new A" },
    { type: "update-note", path: "B.md", oldText: "missing", newText: "new B" }
  ]);

  await assert.rejects(executeOperationPlan(app as unknown as App, plan));

  assert.equal(app.contents.get("A.md"), "old A");
  assert.equal(app.contents.get("B.md"), "old B");
  assert.match(app.audit, /"status":"rolled-back"/);
});

test("没有 local-agent 时拒绝任务完成请求", async () => {
  await assert.rejects(
    () => buildOperationPlan({} as never, { localAgentToken: "" } as never, "完成任务", "vault"),
    /local-agent/
  );
});

class FakeApp {
  readonly contents: Map<string, string>;
  audit = "";
  readonly vault;
  readonly fileManager = {};

  constructor(files: Record<string, string>) {
    this.contents = new Map(Object.entries(files));
    this.vault = {
      getAbstractFileByPath: (path: string) => this.contents.has(path) ? new TFile(path) : null,
      cachedRead: async (file: TFile) => this.contents.get(file.path) ?? "",
      modify: async (file: TFile, content: string) => { this.contents.set(file.path, content); },
      adapter: {
        exists: async (path: string) => path === ".obsidian-agent-data" || (path.endsWith("audit.jsonl") && !!this.audit),
        mkdir: async () => undefined,
        read: async () => this.audit,
        write: async (_path: string, content: string) => { this.audit = content; }
      }
    };
  }
}

async function operationPlan(
  app: FakeApp,
  operations: OperationPlan["operations"]
): Promise<OperationPlan> {
  const expectedHashes: Record<string, string> = {};
  for (const operation of operations) {
    if (operation.type !== "create-note" && operation.type !== "invoke-plugin") {
      expectedHashes[operation.path] = await sha256(app.contents.get(operation.path) ?? "");
    }
  }
  return {
    planId: "plan_test",
    createdAt: new Date().toISOString(),
    expectedHashes,
    summary: "test",
    risk: "low",
    operations
  };
}

async function sha256(content: string): Promise<string> {
  const digest = await webcrypto.subtle.digest("SHA-256", new TextEncoder().encode(content));
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
}
