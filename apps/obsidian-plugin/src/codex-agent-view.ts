import { ItemView, WorkspaceLeaf } from "obsidian";
import { normalizeAgentUrl } from "./url";

export const CODEX_AGENT_VIEW_TYPE = "codex-agent-view";

export class CodeXAgentView extends ItemView {
  constructor(
    leaf: WorkspaceLeaf,
    private readonly getAgentUrl: () => string
  ) {
    super(leaf);
  }

  getViewType(): string {
    return CODEX_AGENT_VIEW_TYPE;
  }

  getDisplayText(): string {
    return "CodeX Agent";
  }

  getIcon(): string {
    return "bot";
  }

  async onOpen(): Promise<void> {
    this.refresh();
  }

  refresh(): void {
    const content = this.containerEl.children[1] as HTMLElement;
    content.empty();
    content.addClass("codex-agent-view");

    const toolbar = content.createDiv({ cls: "codex-agent-toolbar" });
    toolbar.createSpan({ text: "CodeX-Agent" });
    toolbar.createEl("button", { text: "重新加载" }).addEventListener("click", () => this.refresh());

    try {
      const frame = content.createEl("iframe", { cls: "codex-agent-frame" });
      frame.src = normalizeAgentUrl(this.getAgentUrl());
      frame.title = "CodeX-Agent";
      frame.allow = "clipboard-read; clipboard-write";
    } catch (error) {
      content.createDiv({
        cls: "codex-agent-error",
        text: error instanceof Error ? error.message : "CodeX-Agent 地址无效。"
      });
    }
  }
}
