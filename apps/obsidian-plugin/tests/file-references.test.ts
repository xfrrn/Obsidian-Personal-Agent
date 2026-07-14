import assert from "node:assert/strict";
import test from "node:test";
import type { App } from "obsidian";
import { TFile } from "obsidian";
import { extractFileReferencePaths } from "../src/obsidian/vault-reader";

test("@ file references resolve markdown paths", () => {
  const app = fakeApp([
    "Projects/Agent.md",
    "Areas/Search.md",
    "Archive/Agent Notes.md"
  ]);

  assert.deepEqual(
    extractFileReferencePaths(app, "compare @Agent and @[[Areas/Search.md#BM25|search]]"),
    ["Projects/Agent.md", "Areas/Search.md"]
  );
});

test("@ file references ignore ambiguous basenames", () => {
  const app = fakeApp([
    "A/Index.md",
    "B/Index.md"
  ]);

  assert.deepEqual(extractFileReferencePaths(app, "read @Index"), []);
  assert.deepEqual(extractFileReferencePaths(app, "read @A/Index"), ["A/Index.md"]);
});

function fakeApp(paths: string[]): App {
  return {
    vault: {
      getMarkdownFiles: () => paths.map((path) => new TFile(path))
    }
  } as unknown as App;
}
