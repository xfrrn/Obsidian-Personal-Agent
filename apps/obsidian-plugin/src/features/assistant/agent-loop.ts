import { App } from "obsidian";
import { askLocalAgent } from "../../api/local-agent-client";
import { callModel } from "../../api/model-client";
import { extractFileReferencePaths, loadSources, SourceDocument, getCurrentSource } from "../../obsidian/vault-reader";
import type { AgentSettings } from "../../settings/settings";
import { AgentAnswer, AgentError, AgentTraceStep, inferIntent, isLocalAnalysisQuery, isTaskQuery, parseAgentAnswer } from "../../utils/protocol";
import { selectCandidateNotePaths } from "../knowledge-search/search-notes";
import { ANSWER_PROMPT } from "./prompts";
import type { ChatMessage, QueryScope } from "./types";

export type { QueryScope } from "./types";

export async function judgeIntent(
  _app: App,
  _settings: AgentSettings,
  input: string
): Promise<"ask" | "plan"> {
  const cleanInput = input.trim();
  if (!cleanInput) throw new AgentError("请输入问题或修改请求。");
  return inferIntent(cleanInput);
}

export async function askAgent(
  app: App,
  settings: AgentSettings,
  question: string,
  scope: QueryScope,
  history: ChatMessage[] = [],
  onTrace?: (step: AgentTraceStep) => void
): Promise<AgentAnswer> {
  const cleanQuestion = question.trim();
  if (!cleanQuestion) throw new AgentError("请输入问题。");

  if (settings.localAgentToken) {
    return askLocalAgent(app, settings, cleanQuestion, scope, onTrace);
  }

  if (isTaskQuery(cleanQuestion) || isLocalAnalysisQuery(cleanQuestion)) {
    throw new AgentError("任务和本地分析需要先配对并启动 local-agent。");
  }

  if (scope === "current") {
    const source = await getCurrentSource(app);
    const referenced = await loadSources(app, withoutPath(extractFileReferencePaths(app, cleanQuestion), source.path));
    return answerFromSources(app, settings, cleanQuestion, [source, ...referenced], history);
  }

  const referencedPaths = extractFileReferencePaths(app, cleanQuestion);
  const candidates = await selectCandidateNotePaths(app, settings, questionWithHistory(cleanQuestion, history));
  const paths = uniquePaths([...referencedPaths, ...candidates]);
  if (!paths.length) {
    return { answer: "没有找到足以回答这个问题的相关笔记。", citations: [] };
  }

  const sources = await loadSources(app, paths);
  if (!sources.length) throw new AgentError("候选笔记已经不存在，请重试。");
  return answerFromSources(app, settings, cleanQuestion, sources, history);
}

async function answerFromSources(
  app: App,
  settings: AgentSettings,
  question: string,
  sources: SourceDocument[],
  history: ChatMessage[] = []
): Promise<AgentAnswer> {
  const sourcePaths = new Set(sources.map((source) => source.path));
  const sourceHeadings = new Map(
    sources.map((source) => [source.path, new Set(source.headings)] as const)
  );
  const response = await callModel(app, settings, [
    {
      role: "system",
      content: ANSWER_PROMPT
    },
    ...history.slice(-12),
    {
      role: "user",
      content: `问题：${question}\n\n可用笔记：\n${JSON.stringify(sources)}`
    }
  ]);
  return parseAgentAnswer(response, sourcePaths, sourceHeadings);
}

function questionWithHistory(question: string, history: ChatMessage[]): string {
  const recent = history
    .slice(-6)
    .filter((message) => message.role !== "system")
    .map((message) => `${message.role}: ${message.content}`)
    .join("\n");
  return recent ? `${recent}\nuser: ${question}` : question;
}

function uniquePaths(paths: readonly string[]): string[] {
  return [...new Set(paths)];
}

function withoutPath(paths: readonly string[], path: string): string[] {
  return paths.filter((item) => item !== path);
}
