import { App } from "obsidian";
import { AGENT_VIEW_TYPE } from "../views/assistant-view/assistant-view";

export async function openAssistantView(app: App): Promise<void> {
  await app.workspace.ensureSideLeaf(AGENT_VIEW_TYPE, "right", {
    active: true,
    reveal: true
  });
}
