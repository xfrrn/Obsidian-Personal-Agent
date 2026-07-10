import { App } from "obsidian";
import { callModel } from "../../api/model-client";
import type { AgentSettings } from "../../settings/settings";
import { AgentError, parseJsonObject } from "../../utils/protocol";
import { TOOL_SELECTION_PROMPT } from "./prompts";
import type { QueryScope } from "./types";

export type ReadToolName = "search_notes" | "list_tasks";

export async function chooseReadTool(
  app: App,
  settings: AgentSettings,
  input: string,
  scope: QueryScope
): Promise<ReadToolName> {
  const response = await callModel(app, settings, [
    {
      role: "system",
      content: TOOL_SELECTION_PROMPT
    },
    {
      role: "user",
      content: `范围：${scope}\n用户问题：${input}`
    }
  ]);
  const value = parseJsonObject(response);
  if (value.tool === "search_notes" || value.tool === "list_tasks") return value.tool;
  throw new AgentError("模型没有按要求选择可用工具。");
}
