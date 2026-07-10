import assert from "node:assert/strict";
import test from "node:test";
import { rankCandidateNotes } from "../src/features/knowledge-search/local-rank";

const notes = [
  {
    path: "Archive/Random.md",
    title: "随机记录",
    tags: ["daily"],
    headings: ["流水账"],
    excerpt: "今天处理了一些杂事。"
  },
  {
    path: "Projects/AutoUp/Agent.md",
    title: "AutoUp Agent 设计",
    tags: ["project", "agent"],
    headings: ["检索方案", "Operation Plan"],
    excerpt: "需要优化候选笔记召回和模型精排。"
  },
  {
    path: "Areas/Reading/Search.md",
    title: "搜索系统笔记",
    tags: ["search"],
    headings: ["BM25", "检索算法"],
    excerpt: "介绍倒排索引和打分。"
  }
];

test("本地检索优先匹配标题、标签、路径和摘要", () => {
  const ranked = rankCandidateNotes(notes, "AutoUp 检索", 2);
  assert.deepEqual(
    ranked.map((item) => item.path),
    ["Projects/AutoUp/Agent.md", "Areas/Reading/Search.md"]
  );
});

test("没有命中时保留原顺序作为模型兜底候选", () => {
  const ranked = rankCandidateNotes(notes, "完全不存在的主题", 2);
  assert.deepEqual(
    ranked.map((item) => item.path),
    ["Archive/Random.md", "Projects/AutoUp/Agent.md"]
  );
});
