import {
  ItemView,
  MarkdownRenderer,
  setIcon,
  WorkspaceLeaf
} from "obsidian";
import type { QueryScope } from "../../features/assistant/types";
import type PersonalKnowledgeAgentPlugin from "../../main";
import {
  describeOperation,
  OperationPlan
} from "../../features/operation-preview/operation-executor";
import { AgentAnswer, AgentError } from "../../utils/protocol";

export const AGENT_VIEW_TYPE = "personal-knowledge-agent-view";

export class AssistantView extends ItemView {
  private contextEl!: HTMLElement;
  private modeEl!: HTMLSelectElement;
  private questionEl!: HTMLTextAreaElement;
  private resultEl!: HTMLElement;
  private scopeEl!: HTMLSelectElement;
  private sendButton!: HTMLButtonElement;
  private busy = false;

  constructor(
    leaf: WorkspaceLeaf,
    private readonly agentPlugin: PersonalKnowledgeAgentPlugin
  ) {
    super(leaf);
  }

  getViewType(): string {
    return AGENT_VIEW_TYPE;
  }

  getDisplayText(): string {
    return "个人知识库 Agent";
  }

  getIcon(): string {
    return "bot";
  }

  async onOpen(): Promise<void> {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.addClass("pka-view");

    const header = contentEl.createDiv({ cls: "pka-header" });
    const title = header.createDiv({ cls: "pka-title" });
    title.createEl("h2", { text: "个人知识库 Agent" });
    const status = title.createDiv({ cls: "pka-status" });
    status.createSpan({ cls: "pka-dot" });
    status.createSpan({ text: "当前笔记上下文已加载" });

    const toolbar = contentEl.createDiv({ cls: "pka-toolbar" });
    const modeShell = toolbar.createDiv({ cls: "pka-select-shell" });
    this.modeEl = modeShell.createEl("select", { attr: { id: "pka-mode" } });
    this.modeEl.createEl("option", { text: "自动", value: "auto" });
    this.modeEl.createEl("option", { text: "问答", value: "ask" });
    this.modeEl.createEl("option", { text: "修改计划", value: "plan" });

    const scopeShell = toolbar.createDiv({ cls: "pka-segmented" });
    this.scopeEl = scopeShell.createEl("select", { attr: { id: "pka-query-scope" } });
    this.scopeEl.createEl("option", { text: "当前笔记", value: "current" });
    this.scopeEl.createEl("option", { text: "整个知识库", value: "vault" });

    this.contextEl = contentEl.createDiv({ cls: "pka-context" });
    this.renderContext();

    this.resultEl = contentEl.createDiv({
      cls: "pka-result",
      attr: { "aria-live": "polite" }
    });
    this.renderEmptyState();

    const composer = contentEl.createDiv({ cls: "pka-composer" });
    this.questionEl = composer.createEl("textarea", {
      cls: "pka-question",
      attr: {
        id: "pka-question",
        rows: "3",
        placeholder: "继续追问，或让 Agent 直接修改这篇笔记..."
      }
    });
    const composerBar = composer.createDiv({ cls: "pka-composer-bar" });
    this.sendButton = composerBar.createEl("button", {
      cls: "mod-cta pka-send",
      attr: { "aria-label": "发送" }
    });
    setIcon(this.sendButton, "send");

    this.registerDomEvent(this.sendButton, "click", () => void this.submit());
    this.registerDomEvent(this.modeEl, "change", () => this.renderContext());
    this.registerDomEvent(this.scopeEl, "change", () => this.renderContext());
    this.registerDomEvent(this.questionEl, "keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        void this.submit();
      }
    });
  }

  async onClose(): Promise<void> {
    this.contentEl.empty();
  }

  private async submit(): Promise<void> {
    if (this.busy) return;
    const prompt = this.questionEl.value.trim();
    if (!prompt) return;
    this.questionEl.value = "";
    await this.sendPrompt(prompt);
  }

  private async sendPrompt(prompt: string): Promise<void> {
    if (this.busy) return;
    this.setBusy(true);
    this.resultEl.empty();
    this.renderUserMessage(prompt);
    this.renderLoading(this.busyText());

    try {
      const mode = this.modeEl.value;
      const intent = mode === "auto"
        ? await this.agentPlugin.intent(prompt)
        : mode;
      this.renderLoading(intent === "plan"
        ? "意图判断：修改计划。正在生成修改计划..."
        : "意图判断：问答。正在查找相关笔记...");

      if (intent === "plan") {
        this.renderPlan(await this.agentPlugin.plan(
          prompt,
          this.scopeEl.value as QueryScope
        ));
      } else {
        await this.renderAnswer(await this.agentPlugin.ask(
          prompt,
          this.scopeEl.value as QueryScope
        ));
      }
    } catch (error) {
      this.resultEl.querySelector(".pka-loading")?.remove();
      this.resultEl.createDiv({
        cls: "pka-error",
        text: error instanceof AgentError
          ? error.message
          : "处理失败，请稍后重试。"
      });
    } finally {
      this.setBusy(false);
    }
  }

  private async renderAnswer(answer: AgentAnswer): Promise<void> {
    this.resultEl.querySelector(".pka-loading")?.remove();
    this.renderContext(answer.citations.length);
    const card = this.createAssistantCard();
    const answerEl = card.createDiv({ cls: "pka-answer markdown-rendered" });
    await MarkdownRenderer.render(
      this.app,
      answer.answer,
      answerEl,
      answer.citations[0]?.path ?? "",
      this
    );

    if (!answer.citations.length) return;
    const citationsEl = card.createDiv({ cls: "pka-citations" });
    const title = citationsEl.createDiv({ cls: "pka-section-title" });
    title.createSpan({ text: "引用依据" });

    for (const citation of answer.citations) {
      const label = citation.heading
        ? `${citation.path} › ${citation.heading}`
        : citation.path;
      const button = citationsEl.createEl("button", {
        cls: "pka-citation",
        text: label
      });
      this.registerDomEvent(button, "click", () => {
        const link = citation.heading
          ? `${citation.path}#${citation.heading}`
          : citation.path;
        void this.app.workspace.openLinkText(link, "", false);
      });
    }
  }

  private renderPlan(plan: OperationPlan): void {
    this.resultEl.querySelector(".pka-loading")?.remove();
    const card = this.createAssistantCard();
    card.createEl("p", { text: `${plan.summary}（风险：${plan.risk}）` });

    const title = card.createDiv({ cls: "pka-section-title" });
    title.createSpan({ text: `执行计划（${plan.operations.length} 步）` });
    const list = card.createEl("ol", { cls: "pka-plan" });
    for (const operation of plan.operations) {
      const item = list.createEl("li");
      item.createDiv({ text: describeOperation(operation) });
      item.createEl("pre", { text: JSON.stringify(operation, null, 2) });
    }

    const actions = card.createDiv({ cls: "pka-card-actions" });
    const executeButton = actions.createEl("button", {
      cls: "mod-cta pka-send",
      text: "确认执行"
    });
    this.registerDomEvent(executeButton, "click", () =>
      void this.executePlan(plan, executeButton)
    );
  }

  private async executePlan(
    plan: OperationPlan,
    executeButton: HTMLButtonElement
  ): Promise<void> {
    if (this.busy) return;
    this.setBusy(true);
    executeButton.disabled = true;
    executeButton.setText("执行中...");

    try {
      const results = await this.agentPlugin.executePlan(plan);
      const log = this.resultEl.createDiv({ cls: "pka-execution-log" });
      const title = log.createDiv({ cls: "pka-section-title" });
      title.createSpan({ text: `执行日志（已执行 ${results.length} 次）` });
      const list = log.createEl("ul", { cls: "pka-plan" });
      for (const result of results) list.createEl("li", { text: result });
    } catch (error) {
      this.resultEl.createDiv({
        cls: "pka-error",
        text: error instanceof AgentError
          ? error.message
          : "执行失败，请检查笔记状态后重试。"
      });
      executeButton.disabled = false;
      executeButton.setText("确认执行");
    } finally {
      this.setBusy(false);
    }
  }

  private setBusy(busy: boolean): void {
    this.busy = busy;
    this.sendButton.disabled = busy;
    this.modeEl.disabled = busy;
    this.scopeEl.disabled = busy;
    this.questionEl.disabled = busy;
    this.sendButton.empty();
    setIcon(this.sendButton, busy ? "loader" : "send");
  }

  private busyText(): string {
    if (this.modeEl.value === "auto") return "正在判断意图...";
    return this.modeEl.value === "plan"
      ? "正在生成修改计划..."
      : "正在查找相关笔记...";
  }

  private renderContext(citations = 0): void {
    this.contextEl.empty();
    const file = this.app.workspace.getActiveFile();
    this.addChip(this.contextEl, `当前文件：${file?.path ?? "未打开 Markdown"}`);
    this.addChip(this.contextEl, `${citations} 个引用`);
    this.addChip(this.contextEl, `模式：${this.modeEl.selectedOptions[0]?.text ?? "自动"}`);
  }

  private renderEmptyState(): void {
    const card = this.resultEl.createDiv({ cls: "pka-empty" });
    const title = card.createDiv({ cls: "pka-section-title" });
    title.createSpan({ text: "准备好了" });
    card.createEl("p", { text: "选择范围后提问，或直接让 Agent 生成修改计划。" });
  }

  private renderUserMessage(text: string): void {
    const turn = this.resultEl.createDiv({ cls: "pka-user-turn" });
    const bubble = turn.createDiv({ cls: "pka-user-message" });
    const actions = turn.createDiv({ cls: "pka-user-actions" });
    const sentAt = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

    const renderReadMode = () => {
      bubble.empty();
      actions.empty();
      bubble.createEl("p", { text });
      actions.createSpan({ cls: "pka-time", text: sentAt });

      const copyButton = actions.createEl("button", {
        cls: "pka-user-action",
        attr: { "aria-label": "复制消息" }
      });
      setIcon(copyButton, "copy");
      this.registerDomEvent(copyButton, "click", () => {
        void navigator.clipboard.writeText(text);
      });

      const editButton = actions.createEl("button", {
        cls: "pka-user-action",
        attr: { "aria-label": "编辑后重新发送" }
      });
      setIcon(editButton, "pencil");
      this.registerDomEvent(editButton, "click", renderEditMode);
    };

    const renderEditMode = () => {
      bubble.empty();
      actions.empty();
      const editor = bubble.createEl("textarea", {
        cls: "pka-user-edit",
        text
      });
      const editActions = bubble.createDiv({ cls: "pka-edit-actions" });
      const cancelButton = editActions.createEl("button", {
        cls: "pka-edit-cancel",
        text: "取消"
      });
      const sendButton = editActions.createEl("button", {
        cls: "mod-cta pka-edit-send",
        text: "发送"
      });

      this.registerDomEvent(cancelButton, "click", () => {
        renderReadMode();
      });
      this.registerDomEvent(sendButton, "click", () => {
        const edited = editor.value.trim();
        if (edited) void this.sendPrompt(edited);
      });
      this.registerDomEvent(editor, "keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          const edited = editor.value.trim();
          if (edited) void this.sendPrompt(edited);
        }
      });
      editor.focus();
      editor.setSelectionRange(editor.value.length, editor.value.length);
    };

    renderReadMode();
  }

  private renderLoading(text: string): void {
    this.resultEl.querySelector(".pka-loading")?.remove();
    const loading = this.resultEl.createDiv({ cls: "pka-loading" });
    loading.createSpan({ text });
  }

  private createAssistantCard(): HTMLElement {
    const card = this.resultEl.createDiv({ cls: "pka-message pka-agent-card" });
    const meta = card.createDiv({ cls: "pka-message-meta" });
    meta.createSpan({ text: "个人知识库 Agent" });
    meta.createSpan({
      cls: "pka-time",
      text: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    });
    return card;
  }

  private addChip(parent: HTMLElement, text: string): void {
    const chip = parent.createDiv({ cls: "pka-chip" });
    chip.createSpan({ text });
  }
}
