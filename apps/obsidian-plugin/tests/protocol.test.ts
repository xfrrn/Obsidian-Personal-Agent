import assert from "node:assert/strict";
import test from "node:test";
import {
  AgentError,
  chatCompletionsUrl,
  extractChatContent,
  parseAgentAnswer,
  parseCandidatePaths,
  inferIntent,
  isTaskCompletionRequest,
  isLocalAnalysisQuery,
  isTaskQuery,
  parseIntent
} from "../src/utils/protocol";

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

test("路由判断只接受回答或行动，并兼容旧 ask/plan", () => {
  assert.equal(parseIntent("{\"intent\":\"answer\"}"), "answer");
  assert.equal(parseIntent("{\"intent\":\"ask\"}"), "answer");
  assert.equal(parseIntent("```json\n{\"intent\":\"act\"}\n```"), "act");
  assert.equal(parseIntent("```json\n{\"intent\":\"plan\"}\n```"), "act");
  assert.throws(
    () => parseIntent("{\"intent\":\"delete\"}"),
    AgentError
  );
});

test("本地意图判断不会把提问误当成写操作", () => {
  assert.equal(inferIntent("创建一篇项目笔记"), "act");
  assert.equal(inferIntent("你能帮我创建目录吗"), "act");
  assert.equal(inferIntent("新建文件夹 03-Learning/网络与安全"), "act");
  assert.equal(inferIntent("把 A.md 移入废纸篓"), "act");
  assert.equal(inferIntent("帮我把今天需要完成的任务标记为完成"), "act");
  assert.equal(inferIntent("如何创建一篇项目笔记？"), "answer");
  assert.equal(inferIntent("总结当前笔记"), "answer");
  assert.equal(isTaskCompletionRequest("列出今天需要完成的任务"), false);
  assert.equal(isTaskCompletionRequest("把今天需要完成的任务标记为完成"), true);
  assert.equal(isTaskQuery("列出未完成任务"), true);
  assert.equal(isTaskQuery("总结当前笔记"), false);
  assert.equal(isLocalAnalysisQuery("检查当前笔记是否符合规范"), true);
  assert.equal(isLocalAnalysisQuery("给知识库做一次健康检查"), true);
  assert.equal(isLocalAnalysisQuery("总结当前笔记"), false);
});

test("JSON 对象外的额外文本会被拒绝", () => {
  assert.throws(() => parseIntent('说明：{"intent":"ask"}'), AgentError);
  assert.equal(parseIntent('```json\n{"intent":"ask"}\n```'), "answer");
});
