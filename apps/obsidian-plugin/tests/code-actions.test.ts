import assert from "node:assert/strict"
import test from "node:test"
import { codeDownloadName, writeCodeToClipboard } from "../ui/src/code-actions"

test("代码块操作兼容受限剪贴板并生成安全文件名", async () => {
  let modernValue = ""
  await writeCodeToClipboard("legacy", () => true, async (value) => { modernValue = value })
  assert.equal(modernValue, "")

  await writeCodeToClipboard("fallback", () => false, async (value) => { modernValue = value })
  assert.equal(modernValue, "fallback")
  assert.equal(codeDownloadName("json"), "file.json")
  assert.equal(codeDownloadName("../bad lang"), "file...badlang")
  assert.equal(codeDownloadName(), "file.txt")
})
