import { App } from "obsidian";
import { callModel } from "../../api/model-client";
import type { AgentSettings } from "../../settings/settings";
import { AgentError, parseCandidatePaths } from "../../utils/protocol";
import { getVaultCatalog } from "../../obsidian/vault-reader";
import { NOTE_SELECTION_PROMPT } from "../assistant/prompts";

export async function selectCandidateNotePaths(
  app: App,
  settings: AgentSettings,
  question: string
): Promise<string[]> {
  const { catalog, paths } = await getVaultCatalog(app);
  if (!paths.length) throw new AgentError("知识库中没有 Markdown 笔记。");

  const selection = await callModel(app, settings, [
    {
      role: "system",
      content: NOTE_SELECTION_PROMPT
    },
    {
      role: "user",
      content: `问题：${question}\n\n知识库目录：\n${catalog}`
    }
  ]);

  return parseCandidatePaths(selection, new Set(paths), 8);
}
