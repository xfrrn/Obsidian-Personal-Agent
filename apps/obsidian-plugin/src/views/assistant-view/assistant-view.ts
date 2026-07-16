import {
  ItemView,
  MarkdownRenderer,
  Modal,
  setIcon,
  WorkspaceLeaf
} from "obsidian";
import { updateLocalAgentPolicy } from "../../api/local-agent-client";
import type { ChatMessage, QueryScope } from "../../features/assistant/types";
import type PersonalKnowledgeAgentPlugin from "../../main";
import {
  describeOperation,
  OperationPlan
} from "../../features/operation-preview/operation-executor";
import type { ExecutionMode } from "../../settings/settings";
import { AgentAnswer, AgentError, AgentTraceStep } from "../../utils/protocol";

export const AGENT_VIEW_TYPE = "personal-knowledge-agent-view";

const APPROVAL_MODES: ReadonlyArray<{
  value: ExecutionMode;
  label: string;
  menuLabel: string;
  description: string;
  icon: string;
}> = [
  {
    value: "confirm_all",
    label: "请求批准",
    menuLabel: "请求批准",
    description: "编辑文件时始终向你询问",
    icon: "hand"
  },
  {
    value: "risk_based",
    label: "替我审批",
    menuLabel: "替我审批",
    description: "仅对检测到的风险操作请求批准",
    icon: "shield-check"
  },
  {
    value: "unattended",
    label: "完全访问",
    menuLabel: "完全访问权限",
    description: "无需确认即可访问 Vault 中的文件",
    icon: "shield"
  }
];

interface FileSuggestRange {
  range: Range;
  query: string;
}

export class AssistantView extends ItemView {
  private questionEl!: HTMLElement;
  private resultEl!: HTMLElement;
  private sendButton!: HTMLButtonElement;
  private busy = false;
  private fileSuggestEl?: HTMLElement;
  private fileSuggestIndex = 0;
  private fileSuggestItems: string[] = [];
  private fileSuggestRange?: FileSuggestRange;
  private liveTraceDetails?: HTMLDetailsElement;
  private liveTraceList?: HTMLOListElement;
  private liveTraceSummary?: HTMLElement;
  private liveTraceTimer?: number;
  private liveTraceStartedAt = 0;
  private liveTraceSteps: AgentTraceStep[] = [];
  private history: ChatMessage[] = [];
  private pendingActionPrompt?: string;
  private queryScope: QueryScope = "vault";

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

    const toolbar = contentEl.createDiv({ cls: "pka-toolbar" });
    toolbar.createSpan({ cls: "pka-mode-label", text: "自动" });
    const scopeSelect = toolbar.createEl("select", {
      cls: "pka-scope-select",
      attr: { "aria-label": "查询范围" }
    });
    scopeSelect.createEl("option", { value: "vault", text: "全知识库" });
    scopeSelect.createEl("option", { value: "current", text: "当前笔记" });
    scopeSelect.value = this.queryScope;
    this.registerDomEvent(scopeSelect, "change", () => {
      this.queryScope = scopeSelect.value === "current" ? "current" : "vault";
    });

    this.resultEl = contentEl.createDiv({
      cls: "pka-result",
      attr: { "aria-live": "polite" }
    });
    this.renderEmptyState();

    const composer = contentEl.createDiv({ cls: "pka-composer" });
    this.questionEl = composer.createDiv({
      cls: "pka-question",
      attr: {
        id: "pka-question",
        contenteditable: "true",
        role: "textbox",
        "aria-multiline": "true",
        placeholder: "继续追问，或让 Agent 整理笔记..."
      }
    });
    this.questionEl.dataset.placeholder = this.questionEl.getAttribute("placeholder") ?? "";
    this.questionEl.removeAttribute("placeholder");
    this.fileSuggestEl = composer.createDiv({ cls: "pka-file-suggest" });
    this.fileSuggestEl.hide();
    const composerBar = composer.createDiv({ cls: "pka-composer-bar" });
    this.renderApprovalMenu(composerBar);
    this.sendButton = composerBar.createEl("button", {
      cls: "mod-cta pka-send",
      attr: { "aria-label": "发送" }
    });
    setIcon(this.sendButton, "send");

    this.registerDomEvent(this.sendButton, "click", () => void this.submit());
    this.registerDomEvent(this.questionEl, "input", () => this.updateFileSuggest());
    this.registerDomEvent(this.questionEl, "click", () => this.updateFileSuggest());
    this.registerDomEvent(this.questionEl, "keydown", (event) => {
      if (this.handleFileSuggestKey(event)) return;
      if (this.handleReferenceDelete(event)) return;
      if (event.key === "Enter" && event.shiftKey) {
        event.preventDefault();
        document.execCommand("insertLineBreak");
        return;
      }
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        void this.submit();
      }
    });
  }

  private renderApprovalMenu(parent: HTMLElement): void {
    const details = parent.createEl("details", { cls: "pka-approval-menu" });
    const summary = details.createEl("summary", {
      attr: { "aria-label": "选择审批方式" }
    });
    const shield = summary.createSpan({ cls: "pka-approval-summary-icon" });
    setIcon(shield, "shield");
    const currentLabel = summary.createSpan({ cls: "pka-approval-summary-label" });
    const chevron = summary.createSpan({ cls: "pka-approval-chevron" });
    setIcon(chevron, "chevron-up");

    const popover = details.createDiv({
      cls: "pka-approval-popover",
      attr: { role: "menu", "aria-label": "审批方式" }
    });
    popover.createDiv({ cls: "pka-approval-title", text: "应如何批准 Agent 操作？" });

    const rows: Array<{ mode: ExecutionMode; button: HTMLButtonElement; check: HTMLElement }> = [];
    for (const mode of APPROVAL_MODES) {
      const button = popover.createEl("button", {
        cls: "pka-approval-option",
        attr: { role: "menuitemradio", "aria-checked": "false" }
      });
      const icon = button.createSpan({ cls: "pka-approval-option-icon" });
      setIcon(icon, mode.icon);
      const copy = button.createDiv({ cls: "pka-approval-copy" });
      copy.createDiv({ cls: "pka-approval-option-label", text: mode.menuLabel });
      copy.createDiv({ cls: "pka-approval-description", text: mode.description });
      const check = button.createSpan({ cls: "pka-approval-check" });
      rows.push({ mode: mode.value, button, check });
      this.registerDomEvent(button, "click", () => {
        this.agentPlugin.settings.executionMode = mode.value;
        refresh();
        details.open = false;
        void this.agentPlugin.saveSettings()
          .then(() => updateLocalAgentPolicy(this.app, this.agentPlugin.settings));
      });
    }

    const refresh = () => {
      const selected = APPROVAL_MODES.find(
        (mode) => mode.value === this.agentPlugin.settings.executionMode
      ) ?? APPROVAL_MODES[0];
      currentLabel.setText(selected.label);
      for (const row of rows) {
        const active = row.mode === selected.value;
        row.button.toggleClass("is-active", active);
        row.button.setAttribute("aria-checked", String(active));
        row.check.empty();
        if (active) setIcon(row.check, "check");
      }
    };
    refresh();
  }

  async onClose(): Promise<void> {
    this.clearLiveTraceTimer();
    this.contentEl.empty();
  }

  private async submit(): Promise<void> {
    if (this.busy) return;
    const input = this.promptText().trim();
    if (!input) return;
    this.questionEl.empty();
    this.hideFileSuggest();
    if (this.pendingActionPrompt) {
      const prompt = `${this.pendingActionPrompt}\n补充信息：${input}`;
      this.pendingActionPrompt = undefined;
      this.renderUserMessage(input);
      await this.sendPrompt(prompt, false);
      return;
    }
    await this.sendPrompt(input);
  }

  private updateFileSuggest(): void {
    const range = this.currentFileSuggestRange();
    if (!range) {
      this.hideFileSuggest();
      return;
    }

    const needle = range.query.toLocaleLowerCase();
    this.fileSuggestItems = this.app.vault.getMarkdownFiles()
      .map((file) => file.path)
      .sort((a, b) => a.localeCompare(b))
      .filter((path) => {
        const name = (path.split("/").pop() ?? path).replace(/\.md$/i, "");
        const haystack = `${path}\n${name}`.toLocaleLowerCase();
        return !needle || haystack.includes(needle);
      })
      .slice(0, 8);
    this.fileSuggestRange = range;
    this.fileSuggestIndex = 0;
    this.renderFileSuggest();
  }

  private currentFileSuggestRange(): FileSuggestRange | null {
    const range = this.currentSelectionRange();
    if (!range || !(range.startContainer instanceof Text)) return null;
    const before = range.startContainer.data.slice(0, range.startOffset);
    const match = /(^|[\s([{])@([^\s@]*)$/.exec(before);
    if (!match || match[2].includes("]]")) return null;
    const replaceRange = document.createRange();
    replaceRange.setStart(range.startContainer, range.startOffset - match[2].length - 1);
    replaceRange.setEnd(range.startContainer, range.startOffset);
    return { range: replaceRange, query: match[2].replace(/^\[\[/, "") };
  }

  private renderFileSuggest(): void {
    const container = this.fileSuggestEl;
    if (!container || !this.fileSuggestItems.length) {
      this.hideFileSuggest();
      return;
    }
    container.empty();
    for (const [index, path] of this.fileSuggestItems.entries()) {
      const button = container.createEl("button", {
        cls: `pka-file-suggest-item${index === this.fileSuggestIndex ? " is-active" : ""}`,
        text: path
      });
      button.onmousedown = (event) => event.preventDefault();
      button.onclick = () => this.insertFileReference(index);
    }
    container.show();
  }

  private handleFileSuggestKey(event: KeyboardEvent): boolean {
    if (!this.fileSuggestItems.length || !this.fileSuggestEl || this.fileSuggestEl.hidden) return false;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      this.fileSuggestIndex = (this.fileSuggestIndex + delta + this.fileSuggestItems.length) % this.fileSuggestItems.length;
      this.renderFileSuggest();
      return true;
    }
    if (event.key === "Enter" || event.key === "Tab") {
      event.preventDefault();
      this.insertFileReference(this.fileSuggestIndex);
      return true;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      this.hideFileSuggest();
      return true;
    }
    return false;
  }

  private insertFileReference(index: number): void {
    if (!this.fileSuggestRange) return;
    const path = this.fileSuggestItems[index];
    if (!path) return;
    const chip = this.createFileReferenceChip(path);
    this.fileSuggestRange.range.deleteContents();
    this.fileSuggestRange.range.insertNode(chip);
    this.hideFileSuggest();
    this.placeCaretAfter(chip);
  }

  private hideFileSuggest(): void {
    this.fileSuggestItems = [];
    this.fileSuggestRange = undefined;
    this.fileSuggestEl?.hide();
  }

  private createFileReferenceChip(path: string): HTMLElement {
    const chip = document.createElement("span");
    chip.className = "pka-file-ref";
    chip.contentEditable = "false";
    chip.dataset.path = path;
    chip.title = path;
    const mark = document.createElement("span");
    mark.className = "pka-file-ref-mark";
    mark.textContent = "@";
    const label = document.createElement("span");
    label.className = "pka-file-ref-label";
    label.textContent = path;
    chip.append(mark, label);
    chip.onclick = () => this.removeFileReference(chip);
    return chip;
  }

  private promptText(): string {
    let text = "";
    const visit = (node: Node) => {
      if (node instanceof HTMLElement && node.hasClass("pka-file-ref")) {
        const path = node.dataset.path;
        if (path) text += `@[[${path}]]`;
        return;
      }
      if (node instanceof Text) {
        text += node.data;
        return;
      }
      if (node instanceof HTMLBRElement) text += "\n";
      node.childNodes.forEach(visit);
    };
    this.questionEl.childNodes.forEach(visit);
    return text;
  }

  private handleReferenceDelete(event: KeyboardEvent): boolean {
    if (event.key !== "Backspace" && event.key !== "Delete") return false;
    const range = this.currentSelectionRange();
    if (!range) return false;
    const chip = this.adjacentFileReference(range, event.key === "Backspace" ? "before" : "after");
    if (!chip) return false;
    event.preventDefault();
    this.removeFileReference(chip);
    return true;
  }

  private adjacentFileReference(range: Range, side: "before" | "after"): HTMLElement | null {
    const container = range.startContainer;
    const offset = range.startOffset;
    if (container instanceof Text) {
      if ((side === "before" && offset > 0) || (side === "after" && offset < container.data.length)) return null;
      return this.fileReferenceNear(container, side);
    }
    if (!(container instanceof HTMLElement)) return null;
    const node = container.childNodes[side === "before" ? offset - 1 : offset] ?? null;
    return node instanceof HTMLElement && node.hasClass("pka-file-ref") ? node : null;
  }

  private fileReferenceNear(node: Node, side: "before" | "after"): HTMLElement | null {
    const sibling = side === "before" ? node.previousSibling : node.nextSibling;
    return sibling instanceof HTMLElement && sibling.hasClass("pka-file-ref") ? sibling : null;
  }

  private removeFileReference(chip: HTMLElement): void {
    const next = chip.nextSibling;
    chip.remove();
    if (next) this.placeCaretBefore(next);
    else this.placeCaretAtEnd();
    this.updateFileSuggest();
  }

  private currentSelectionRange(): Range | null {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0) return null;
    const range = selection.getRangeAt(0);
    if (!range.collapsed || !this.questionEl.contains(range.startContainer)) return null;
    return range;
  }

  private placeCaretAfter(node: Node): void {
    const range = document.createRange();
    range.setStartAfter(node);
    range.collapse(true);
    this.setSelection(range);
  }

  private placeCaretBefore(node: Node): void {
    const range = document.createRange();
    range.setStartBefore(node);
    range.collapse(true);
    this.setSelection(range);
  }

  private placeCaretAtEnd(): void {
    const range = document.createRange();
    range.selectNodeContents(this.questionEl);
    range.collapse(false);
    this.setSelection(range);
  }

  private setSelection(range: Range): void {
    this.questionEl.focus();
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
  }

  private missingActionQuestion(prompt: string): string | null {
    if (!/(创建|新建).{0,12}(目录|文件夹)|(目录|文件夹).{0,12}(创建|新建)/.test(prompt)) {
      return null;
    }
    if (/[^\s，。；：！？]+\/[^\s，。；：！？]+/.test(prompt)) return null;
    return "要创建哪个目录？请给我完整路径，比如 `03-Learning/网络与安全`。";
  }

  private async sendPrompt(prompt: string, renderUser = true): Promise<void> {
    if (this.busy) return;
    this.setBusy(true);
    this.resultEl.querySelector(".pka-empty")?.remove();
    this.clearLiveTraceTimer();
    this.liveTraceDetails = undefined;
    this.liveTraceList = undefined;
    this.liveTraceSummary = undefined;
    this.liveTraceStartedAt = 0;
    this.liveTraceSteps = [];
    if (renderUser) this.renderUserMessage(prompt);
    this.startLiveTrace();

    try {
      const queryScope = this.queryScope;
      const intent = await this.agentPlugin.intent(prompt);
      this.renderLiveTrace(this.traceStep("intent", `路由判断：${intent === "act" ? "行动" : "回答"}`));

      if (intent === "act") {
        const missingQuestion = this.missingActionQuestion(prompt);
        if (missingQuestion) {
          this.pendingActionPrompt = prompt;
          this.finishLiveTrace("等待补充");
          await this.renderAnswer({ answer: missingQuestion, citations: [] }, true);
          return;
        }
        const plan = await this.agentPlugin.plan(
          prompt,
          queryScope
        );
        for (const step of plan.trace ?? []) this.renderLiveTrace(step);
        this.finishLiveTrace();
        await this.presentPlan(plan, true);
      } else {
        const answer = await this.agentPlugin.ask(
          prompt,
          queryScope,
          this.history,
          (step) => this.renderLiveTrace(step)
        );
        if (answer.operationPlan) {
          this.finishLiveTrace();
          await this.presentPlan(answer.operationPlan, true);
        } else {
          await this.renderAnswer(answer, this.liveTraceSteps.length > 0);
        }
        this.history.push(
          { role: "user", content: prompt },
          { role: "assistant", content: JSON.stringify({ answer: answer.answer, citations: answer.citations }) }
        );
      }
    } catch (error) {
      this.resultEl.querySelector(".pka-loading")?.remove();
      this.finishLiveTrace("处理失败");
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

  private async renderAnswer(answer: AgentAnswer, skipTrace = false): Promise<void> {
    this.resultEl.querySelector(".pka-loading")?.remove();
    if (skipTrace) this.finishLiveTrace();
    const card = this.createAssistantCard();
    if (!skipTrace) this.renderTrace(card, answer.trace);
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

  private renderTrace(card: HTMLElement, trace: AgentAnswer["trace"]): void {
    if (!trace?.length) return;
    const details = card.createEl("details", { cls: "pka-agent-trace" });
    const summary = details.createEl("summary");
    summary.createSpan({ cls: "pka-trace-summary-text", text: `已处理 ${trace.length} 步` });
    const icon = summary.createSpan({ cls: "pka-trace-icon" });
    setIcon(icon, "chevron-right");

    const list = details.createEl("ol", { cls: "pka-trace-list" });
    for (const step of trace) {
      this.appendTraceStep(list, step);
    }
  }

  private traceStep(toolName: string, summary: string): AgentTraceStep {
    return {
      round: 1,
      toolName,
      status: "completed",
      summary,
      detail: {}
    };
  }

  private renderLiveTrace(step: AgentTraceStep): void {
    this.liveTraceSteps.push(step);
    this.startLiveTrace();
    this.updateLiveTraceSummary();
    this.appendTraceStep(this.liveTraceList!, step);
    this.liveTraceDetails!.scrollIntoView({ block: "nearest" });
  }

  private startLiveTrace(): void {
    if (this.liveTraceDetails && this.liveTraceList) return;
    this.liveTraceStartedAt = Date.now();
    this.liveTraceDetails = this.resultEl.createEl("details", { cls: "pka-agent-trace pka-agent-trace-card is-live" });
    this.liveTraceDetails.open = true;
    const summary = this.liveTraceDetails.createEl("summary");
    this.liveTraceSummary = summary.createSpan({ cls: "pka-trace-summary-text", text: "思考中 0s" });
    const icon = summary.createSpan({ cls: "pka-trace-icon" });
    setIcon(icon, "chevron-right");
    this.liveTraceList = this.liveTraceDetails.createEl("ol", { cls: "pka-trace-list" });
    this.liveTraceTimer = window.setInterval(() => this.updateLiveTraceSummary(), 1000);
  }

  private appendTraceStep(list: HTMLOListElement, step: AgentTraceStep): void {
    const item = list.createEl("li", {
      cls: step.status === "failed" ? "is-error" : "is-ok"
    });
    const header = item.createDiv({ cls: "pka-trace-row" });
    header.createSpan({ cls: "pka-trace-status", text: step.status === "failed" ? "失败" : "已处理" });
    header.createSpan({ cls: "pka-trace-summary", text: step.summary || step.toolName });
    header.createSpan({ cls: "pka-trace-tool", text: step.toolName });
  }

  private finishLiveTrace(label = "已处理"): void {
    if (!this.liveTraceDetails) return;
    this.clearLiveTraceTimer();
    this.liveTraceDetails.open = false;
    this.liveTraceDetails.removeClass("is-live");
    this.liveTraceDetails.addClass("is-done");
    this.liveTraceSummary?.setText(`${label} ${this.formatElapsed()}`);
  }

  private updateLiveTraceSummary(): void {
    this.liveTraceSummary?.setText(`思考中 ${this.formatElapsed()}`);
  }

  private clearLiveTraceTimer(): void {
    if (this.liveTraceTimer === undefined) return;
    window.clearInterval(this.liveTraceTimer);
    this.liveTraceTimer = undefined;
  }

  private formatElapsed(): string {
    const seconds = Math.max(0, Math.round((Date.now() - this.liveTraceStartedAt) / 1000));
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    const rest = seconds % 60;
    return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
  }

  private async presentPlan(plan: OperationPlan, skipTrace = false): Promise<void> {
    const executeButton = this.renderPlan(plan, skipTrace);
    const mode = this.agentPlugin.settings.executionMode;
    const builtInAutoApproval = plan.managedBy !== "local-agent" && (
      mode === "unattended" || (mode === "risk_based" && plan.risk === "low")
    );
    if (plan.requiresConfirmation === false || builtInAutoApproval) {
      this.setBusy(false);
      await this.executePlan(plan, executeButton, false);
      return;
    }
    this.openPlanConfirmation(plan, executeButton);
  }

  private renderPlan(plan: OperationPlan, skipTrace = false): HTMLButtonElement {
    this.resultEl.querySelector(".pka-loading")?.remove();
    const card = this.createAssistantCard();
    if (!skipTrace) this.renderTrace(card, plan.trace);
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
    return executeButton;
  }

  private openPlanConfirmation(plan: OperationPlan, executeButton: HTMLButtonElement): void {
    const modal = new Modal(this.app);
    modal.modalEl.addClass("pka-confirm-modal");
    modal.setTitle("批准执行此操作计划？");
    modal.contentEl.createEl("p", { text: `${plan.summary}（风险：${plan.risk}）` });
    const list = modal.contentEl.createEl("ol", { cls: "pka-confirm-list" });
    for (const operation of plan.operations) {
      list.createEl("li", { text: describeOperation(operation) });
    }
    const actions = modal.contentEl.createDiv({ cls: "pka-confirm-actions" });
    const cancel = actions.createEl("button", { text: "暂不执行" });
    const approve = actions.createEl("button", { cls: "mod-cta", text: "批准并执行" });
    this.registerDomEvent(cancel, "click", () => modal.close());
    this.registerDomEvent(approve, "click", () => {
      modal.close();
      void this.executePlan(plan, executeButton);
    });
    modal.open();
  }

  private async executePlan(
    plan: OperationPlan,
    executeButton: HTMLButtonElement,
    confirmed = true
  ): Promise<void> {
    if (this.busy) return;
    this.setBusy(true);
    executeButton.disabled = true;
    executeButton.setText("执行中...");

    try {
      const results = await this.agentPlugin.executePlan(plan, confirmed);
      const log = this.resultEl.createDiv({ cls: "pka-execution-log" });
      const title = log.createDiv({ cls: "pka-section-title" });
      title.createSpan({ text: `执行日志（已执行 ${results.length} 次）` });
      const list = log.createEl("ul", { cls: "pka-plan" });
      for (const result of results) list.createEl("li", { text: result });
      executeButton.setText("已执行");
      if (plan.managedBy === "local-agent" && executeButton.parentElement) {
        const rollbackButton = executeButton.parentElement.createEl("button", { text: "撤销" });
        this.registerDomEvent(rollbackButton, "click", () =>
          void this.rollbackPlan(plan, rollbackButton)
        );
      }
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

  private async rollbackPlan(plan: OperationPlan, button: HTMLButtonElement): Promise<void> {
    if (this.busy) return;
    this.setBusy(true);
    button.disabled = true;
    button.setText("撤销中...");
    try {
      const results = await this.agentPlugin.rollbackPlan(plan);
      const log = this.resultEl.createDiv({ cls: "pka-execution-log" });
      for (const result of results) log.createDiv({ text: result });
      button.setText("已撤销");
    } catch (error) {
      this.resultEl.createDiv({
        cls: "pka-error",
        text: error instanceof Error ? error.message : "撤销失败。"
      });
      button.disabled = false;
      button.setText("撤销");
    } finally {
      this.setBusy(false);
    }
  }

  private setBusy(busy: boolean): void {
    this.busy = busy;
    this.sendButton.disabled = busy;
    this.questionEl.contentEditable = busy ? "false" : "true";
    this.questionEl.toggleAttribute("aria-disabled", busy);
    this.sendButton.empty();
    setIcon(this.sendButton, busy ? "loader" : "send");
  }

  private renderEmptyState(): void {
    const card = this.resultEl.createDiv({ cls: "pka-empty" });
    const title = card.createDiv({ cls: "pka-section-title" });
    title.createSpan({ text: "今天想整理什么？" });
    card.createEl("p", { text: "可以提问，也可以直接让我生成修改计划。" });
  }

  private renderUserMessage(text: string): void {
    const turn = this.resultEl.createDiv({ cls: "pka-user-turn" });
    const bubble = turn.createDiv({ cls: "pka-user-message" });
    const actions = turn.createDiv({ cls: "pka-user-actions" });
    const sentAt = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const historyIndex = this.history.length;
    let currentText = text;

    const renderReadMode = () => {
      bubble.empty();
      actions.empty();
      bubble.createEl("p", { text: currentText });
      actions.createSpan({ cls: "pka-time", text: sentAt });

      const copyButton = actions.createEl("button", {
        cls: "pka-user-action",
        attr: { "aria-label": "复制消息" }
      });
      setIcon(copyButton, "copy");
      this.registerDomEvent(copyButton, "click", () => {
        void navigator.clipboard.writeText(currentText);
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
        text: currentText
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
        if (edited) void resendEdited(edited);
      });
      this.registerDomEvent(editor, "keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          const edited = editor.value.trim();
          if (edited) void resendEdited(edited);
        }
      });
      editor.focus();
      editor.setSelectionRange(editor.value.length, editor.value.length);
    };

    const resendEdited = async (edited: string): Promise<void> => {
      if (this.busy) return;
      currentText = edited;
      this.history = this.history.slice(0, historyIndex);
      this.removeAfter(turn);
      renderReadMode();
      await this.sendPrompt(edited, false);
    };

    renderReadMode();
  }

  private removeAfter(element: HTMLElement): void {
    let next = element.nextElementSibling;
    while (next) {
      const current = next;
      next = next.nextElementSibling;
      current.remove();
    }
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

}
