import assert from "node:assert/strict";
import test from "node:test";
import {
  AgentError,
  chatCompletionsUrl,
  extractChatContent,
  parseAgentAnswer,
  parseCandidatePaths,
  parseIntent
} from "../src/protocol";

test("API 地址只允许 HTTPS 或本机 HTTP", () => {
  assert.equal(
    chatCompletionsUrl("https://api.example.com/v1/"),
    "https://api.example.com/v1/chat/completions"
  );
  assert.equal(
    chatCompletionsUrl("http://localhost:11434/v1"),
    "http://localhost:11434/v1/chat/completions"
  );
  assert.throws(
    () => chatCompletionsUrl("http://api.example.com/v1"),
    AgentError
  );
});

test("候选路径会过滤未知值、重复值和超额结果", () => {
  const allowed = new Set(["A.md", "B.md", "C.md"]);
  assert.deepEqual(
    parseCandidatePaths(
      "```json\n{\"paths\":[\"A.md\",\"missing.md\",\"A.md\",\"B.md\"]}\n```",
      allowed,
      2
    ),
    ["A.md", "B.md"]
  );
});

test("回答只保留真实笔记引用", () => {
  const answer = parseAgentAnswer(
    JSON.stringify({
      answer: "根据笔记，答案是 **A**。",
      citations: [
        { path: "A.md", heading: "结论" },
        { path: "missing.md" },
        { path: "A.md", heading: "结论" }
      ]
    }),
    new Set(["A.md"]),
    new Map([["A.md", new Set(["结论"])]])
  );
  assert.equal(answer.answer, "根据笔记，答案是 **A**。");
  assert.deepEqual(answer.citations, [{ path: "A.md", heading: "结论" }]);
});

test("不存在的标题不会成为引用锚点", () => {
  const answer = parseAgentAnswer(
    JSON.stringify({
      answer: "回答",
      citations: [{ path: "A.md", heading: "虚构标题" }]
    }),
    new Set(["A.md"]),
    new Map([["A.md", new Set(["真实标题"])]])
  );
  assert.deepEqual(answer.citations, [{ path: "A.md", heading: undefined }]);
});

test("无法识别的模型响应会被拒绝", () => {
  assert.equal(
    extractChatContent({
      choices: [{ message: { content: "ok" } }]
    }),
    "ok"
  );
  assert.throws(
    () => extractChatContent({ choices: [] }),
    AgentError
  );
  assert.throws(
    () => parseAgentAnswer("not json", new Set()),
    AgentError
  );
});

test("意图判断只接受问答或修改计划", () => {
  assert.equal(parseIntent("{\"intent\":\"ask\"}"), "ask");
  assert.equal(parseIntent("```json\n{\"intent\":\"plan\"}\n```"), "plan");
  assert.throws(
    () => parseIntent("{\"intent\":\"delete\"}"),
    AgentError
  );
});
