import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import test from "node:test";
import type { App } from "obsidian";
import { TFile, TFolder } from "obsidian";
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

test("笔记移入废纸篓并管理空目录", async () => {
  const app = new FakeApp({ "A.md": "content" }, ["OldEmpty"]);
  const plan = await operationPlan(app, [
    { type: "trash-note", path: "A.md" },
    { type: "create-folder", path: "NewEmpty" },
    { type: "delete-folder", path: "OldEmpty" }
  ]);

  await executeOperationPlan(app as unknown as App, plan);

  assert.equal(app.contents.has("A.md"), false);
  assert.deepEqual(app.trashed, ["A.md"]);
  assert.equal(app.folders.has("NewEmpty"), true);
  assert.equal(app.folders.has("OldEmpty"), false);
});

test("废纸篓和新目录在后续失败时回滚", async () => {
  const app = new FakeApp({ "A.md": "content", "B.md": "old" });
  const plan = await operationPlan(app, [
    { type: "create-folder", path: "NewEmpty" },
    { type: "trash-note", path: "A.md" },
    { type: "update-note", path: "B.md", oldText: "missing", newText: "new" }
  ]);

  await assert.rejects(executeOperationPlan(app as unknown as App, plan));

  assert.equal(app.contents.get("A.md"), "content");
  assert.equal(app.folders.has("NewEmpty"), false);
});

test("拒绝删除非空目录", async () => {
  const app = new FakeApp({ "Folder/A.md": "content" }, ["Folder"]);
  const plan = await operationPlan(app, [{ type: "delete-folder", path: "Folder" }]);

  await assert.rejects(executeOperationPlan(app as unknown as App, plan), /不是空目录/);

  assert.equal(app.folders.has("Folder"), true);
  assert.equal(app.contents.get("Folder/A.md"), "content");
});

test("没有 local-agent 时拒绝任务完成请求", async () => {
  await assert.rejects(
    () => buildOperationPlan({} as never, { localAgentToken: "" } as never, "完成任务", "vault"),
    /local-agent/
  );
});

class FakeApp {
  readonly contents: Map<string, string>;
  readonly folders: Set<string>;
  readonly trashed: string[] = [];
  audit = "";
  readonly vault;
  readonly fileManager;

  constructor(files: Record<string, string>, folders: string[] = []) {
    this.contents = new Map(Object.entries(files));
    this.folders = new Set(folders);
    this.vault = {
      getAbstractFileByPath: (path: string) => this.abstractFile(path),
      cachedRead: async (file: TFile) => this.contents.get(file.path) ?? "",
      modify: async (file: TFile, content: string) => { this.contents.set(file.path, content); },
      create: async (path: string, content: string) => {
        this.contents.set(path, content);
        return new TFile(path);
      },
      createFolder: async (path: string) => {
        this.folders.add(path);
        return this.abstractFile(path) as TFolder;
      },
      delete: async (file: TFile | TFolder) => {
        if (file instanceof TFile) this.contents.delete(file.path);
        else this.folders.delete(file.path);
      },
      adapter: {
        exists: async (path: string) => path === ".obsidian-agent-data" || (path.endsWith("audit.jsonl") && !!this.audit),
        mkdir: async () => undefined,
        read: async () => this.audit,
        write: async (_path: string, content: string) => { this.audit = content; }
      }
    };
    this.fileManager = {
      trashFile: async (file: TFile) => {
        this.trashed.push(file.path);
        this.contents.delete(file.path);
      }
    };
  }

  private abstractFile(path: string): TFile | TFolder | null {
    if (this.contents.has(path)) return new TFile(path);
    if (!this.folders.has(path)) return null;
    const folder = new TFolder(path);
    const prefix = `${path}/`;
    folder.children = [
      ...[...this.contents.keys()]
        .filter((item) => item.startsWith(prefix) && !item.slice(prefix.length).includes("/"))
        .map((item) => new TFile(item)),
      ...[...this.folders]
        .filter((item) => item.startsWith(prefix) && !item.slice(prefix.length).includes("/"))
        .map((item) => new TFolder(item))
    ];
    return folder;
  }
}

async function operationPlan(
  app: FakeApp,
  operations: OperationPlan["operations"]
): Promise<OperationPlan> {
  const expectedHashes: Record<string, string> = {};
  for (const operation of operations) {
    if (
      operation.type !== "create-note" &&
      operation.type !== "create-folder" &&
      operation.type !== "delete-folder" &&
      operation.type !== "invoke-plugin"
    ) {
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
