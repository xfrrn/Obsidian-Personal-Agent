import { App } from "obsidian";
import { callModel } from "../../api/model-client";
import type { AgentSettings } from "../../settings/settings";
import { AgentError, parseCandidatePaths } from "../../utils/protocol";
import { getVaultCatalogItems } from "../../obsidian/vault-reader";
import { NOTE_SELECTION_PROMPT } from "../assistant/prompts";
import { rankCandidateNotes } from "./local-rank";

const MODEL_CANDIDATE_LIMIT = 30;

export async function selectCandidateNotePaths(
  app: App,
  settings: AgentSettings,
  question: string,
  systemPrompt = NOTE_SELECTION_PROMPT,
  inputLabel = "问题"
): Promise<string[]> {
  const { items, paths } = await getVaultCatalogItems(app);
  if (!paths.length) throw new AgentError("知识库中没有 Markdown 笔记。");
  const candidates = rankCandidateNotes(items, question, MODEL_CANDIDATE_LIMIT);
  const candidatePaths = candidates.map((item) => item.path);

  const selection = await callModel(app, settings, [
    {
      role: "system",
      content: systemPrompt
    },
    {
      role: "user",
      content: `${inputLabel}：${question}\n\n候选笔记：\n${JSON.stringify(candidates)}`
    }
  ]);

  return parseCandidatePaths(selection, new Set(candidatePaths), 8);
}
