import { App } from "obsidian";
import { callModel } from "../../api/model-client";
import { loadSources, SourceDocument, getCurrentSource } from "../../obsidian/vault-reader";
import type { AgentSettings } from "../../settings/settings";
import { AgentAnswer, AgentError, parseAgentAnswer, parseIntent } from "../../utils/protocol";
import { selectCandidateNotePaths } from "../knowledge-search/search-notes";
import { answerWithTasks } from "../task-actions/list-tasks";
import { ANSWER_PROMPT, INTENT_PROMPT } from "./prompts";
import { chooseReadTool } from "./tool-router";
import type { QueryScope } from "./types";

export type { QueryScope } from "./types";

export async function judgeIntent(
  app: App,
  settings: AgentSettings,
  input: string
): Promise<"ask" | "plan"> {
  const cleanInput = input.trim();
  if (!cleanInput) throw new AgentError("请输入问题或修改请求。");

  const response = await callModel(app, settings, [
    {
      role: "system",
      content: INTENT_PROMPT
    },
    {
      role: "user",
      content: cleanInput
    }
  ]);
  return parseIntent(response);
}

export async function askAgent(
  app: App,
  settings: AgentSettings,
  question: string,
  scope: QueryScope
): Promise<AgentAnswer> {
  const cleanQuestion = question.trim();
  if (!cleanQuestion) throw new AgentError("请输入问题。");

  const tool = await chooseReadTool(app, settings, cleanQuestion, scope);
  if (tool === "list_tasks") return answerWithTasks(app, cleanQuestion, scope);

  if (scope === "current") {
    const source = await getCurrentSource(app);
    return answerFromSources(app, settings, cleanQuestion, [source]);
  }

  const candidates = await selectCandidateNotePaths(app, settings, cleanQuestion);
  if (!candidates.length) {
    return { answer: "没有找到足以回答这个问题的相关笔记。", citations: [] };
  }

  const sources = await loadSources(app, candidates);
  if (!sources.length) throw new AgentError("候选笔记已经不存在，请重试。");
  return answerFromSources(app, settings, cleanQuestion, sources);
}

async function answerFromSources(
  app: App,
  settings: AgentSettings,
  question: string,
  sources: SourceDocument[]
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
    {
      role: "user",
      content: `问题：${question}\n\n可用笔记：\n${JSON.stringify(sources)}`
    }
  ]);
  return parseAgentAnswer(response, sourcePaths, sourceHeadings);
}
