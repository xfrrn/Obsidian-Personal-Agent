import {
  ItemView,
  MarkdownRenderer,
  Notice,
  setIcon,
  WorkspaceLeaf
} from "obsidian";
import type PersonalKnowledgeAgentPlugin from "../../main";
import {
  archiveConversation,
  createConversation,
  listConversations,
  loadConversation,
  resolveApproval,
  streamConversation,
  toOperationPlan,
  type AgentEvent,
  type AgentMode,
  type AgentPlanState,
  type ConversationSummary,
  type QueryScope
} from "../../api/local-agent-client";
import {
  describeOperation,
  type OperationPlan
} from "../../api/operation-plan";

export const AGENT_VIEW_TYPE = "personal-knowledge-agent-view";

type MessageRole = "user" | "assistant" | "error" | "notice";
type ToolState = "running" | "success" | "error" | "interrupted";

interface ViewMessage {
  id: string;
  role: MessageRole;
  text: string;
  createdAt: number;
  streaming?: boolean;
}

interface ToolTrace {
  id: string;
  name: string;
  arguments?: unknown;
  state: ToolState;
}

interface RunTrace {
  submissionId?: number;
  startedAt: number;
  completedAt?: number;
  tools: ToolTrace[];
}

interface ApprovalRequest {
  callId: string;
  submissionId: number;
  name: string;
  command: string;
  justification: string;
  resolving?: boolean;
}

export class AssistantView extends ItemView {
  private conversations: ConversationSummary[] = [];
  private activeConversationId = "";
  private messages: ViewMessage[] = [];
  private traces: RunTrace[] = [];
  private approvals: ApprovalRequest[] = [];
  private agentPlan: AgentPlanState | null = null;
  private operationPlan: OperationPlan | null = null;
  private mode: AgentMode = "default";
  private queryScope: QueryScope = "current";
  private requestVersion = 0;
  private loaded = false;

  private statusEl!: HTMLElement;
  private sessionSelect!: HTMLSelectElement;
  private modeSelect!: HTMLSelectElement;
  private scopeSelect!: HTMLSelectElement;
  private planEl!: HTMLElement;
  private timelineEl!: HTMLElement;
  private approvalEl!: HTMLElement;
  private operationEl!: HTMLElement;
  private inputEl!: HTMLTextAreaElement;

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
    this.buildShell();
    await this.reload();
    this.registerInterval(window.setInterval(() => {
      if (!this.loaded && this.agentPlugin.settings.localAgentToken) void this.reload();
    }, 3_000));
  }

  async onClose(): Promise<void> {
    this.requestVersion += 1;
  }

  private buildShell(): void {
    const root = this.containerEl.children[1] as HTMLElement;
    root.empty();
    root.addClass("pka-agent-view");

    const header = root.createDiv({ cls: "pka-agent-header" });
    const title = header.createDiv({ cls: "pka-agent-title" });
    const icon = title.createSpan({ cls: "pka-agent-title-icon" });
    setIcon(icon, "bot");
    title.createSpan({ text: "知识库 Agent" });
    this.statusEl = header.createDiv({ cls: "pka-agent-status", text: "正在连接…" });

    const sessionBar = root.createDiv({ cls: "pka-session-bar" });
    this.sessionSelect = sessionBar.createEl("select", { attr: { "aria-label": "当前会话" } });
    this.sessionSelect.onchange = () => void this.selectConversation(this.sessionSelect.value);
    this.iconButton(sessionBar, "plus", "新建会话", () => void this.newConversation());
    this.iconButton(sessionBar, "archive", "归档会话", () => void this.archiveCurrent());

    const controls = root.createDiv({ cls: "pka-agent-controls" });
    this.scopeSelect = controls.createEl("select", { attr: { "aria-label": "知识范围" } });
    this.scopeSelect.createEl("option", { value: "current", text: "当前笔记" });
    this.scopeSelect.createEl("option", { value: "vault", text: "整个知识库" });
    this.scopeSelect.value = this.queryScope;
    this.scopeSelect.onchange = () => this.queryScope = this.scopeSelect.value as QueryScope;
    this.modeSelect = controls.createEl("select", { attr: { "aria-label": "Agent 模式" } });
    this.modeSelect.createEl("option", { value: "default", text: "执行模式" });
    this.modeSelect.createEl("option", { value: "plan", text: "规划模式" });
    this.modeSelect.onchange = () => this.mode = this.modeSelect.value as AgentMode;

    this.planEl = root.createDiv({ cls: "pka-agent-plan" });
    this.timelineEl = root.createDiv({ cls: "pka-agent-timeline" });
    this.approvalEl = root.createDiv({ cls: "pka-agent-approvals" });
    this.operationEl = root.createDiv({ cls: "pka-operation-panel" });

    const composer = root.createDiv({ cls: "pka-agent-composer" });
    this.inputEl = composer.createEl("textarea", {
      attr: { placeholder: "询问或整理你的知识库…", rows: "3", "aria-label": "消息" }
    });
    this.inputEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        void this.send();
      }
    });
    const sendButton = composer.createEl("button", { cls: "pka-send-button", attr: { "aria-label": "发送" } });
    setIcon(sendButton, "send-horizontal");
    sendButton.onclick = () => void this.send();
  }

  private iconButton(parent: HTMLElement, iconName: string, label: string, action: () => void): void {
    const button = parent.createEl("button", { cls: "pka-icon-button", attr: { "aria-label": label, title: label } });
    setIcon(button, iconName);
    button.onclick = action;
  }

  private async reload(): Promise<void> {
    if (!this.agentPlugin.settings.localAgentToken) {
      this.setStatus("请先启动并连接 local-agent", true);
      return;
    }
    try {
      this.conversations = await listConversations(this.agentPlugin.settings);
      const preferred = this.agentPlugin.settings.activeConversationId;
      const active = this.conversations.find((item) => item.id === preferred)
        ?? this.conversations[0]
        ?? await createConversation(this.agentPlugin.settings);
      if (!this.conversations.some((item) => item.id === active.id)) this.conversations.unshift(active);
      await this.selectConversation(active.id);
      this.loaded = true;
      this.setStatus("已连接");
    } catch (error) {
      this.loaded = false;
      this.setStatus(errorText(error), true);
    }
  }

  private async selectConversation(id: string): Promise<void> {
    if (!id) return;
    const version = ++this.requestVersion;
    try {
      const detail = await loadConversation(this.agentPlugin.settings, id);
      if (version !== this.requestVersion) return;
      this.activeConversationId = id;
      this.mode = detail.session.mode;
      this.modeSelect.value = this.mode;
      this.agentPlan = detail.session.plan;
      this.operationPlan = detail.pendingOperationPlan ?? null;
      this.messages = detail.messages.map((message) => ({
        id: message.id,
        role: message.role,
        text: message.text,
        createdAt: message.created_at
      }));
      this.traces = [];
      this.approvals = [];
      await this.agentPlugin.setActiveConversation(id);
      this.renderSessions();
      this.renderAll();
    } catch (error) {
      this.setStatus(errorText(error), true);
    }
  }

  private async newConversation(): Promise<void> {
    try {
      const conversation = await createConversation(this.agentPlugin.settings);
      this.conversations.unshift(conversation);
      await this.selectConversation(conversation.id);
    } catch (error) {
      new Notice(errorText(error));
    }
  }

  private async archiveCurrent(): Promise<void> {
    if (!this.activeConversationId) return;
    try {
      await archiveConversation(this.agentPlugin.settings, this.activeConversationId);
      this.conversations = this.conversations.filter((item) => item.id !== this.activeConversationId);
      const next = this.conversations[0] ?? await createConversation(this.agentPlugin.settings);
      if (!this.conversations.length) this.conversations.push(next);
      await this.selectConversation(next.id);
    } catch (error) {
      new Notice(errorText(error));
    }
  }

  private renderSessions(): void {
    this.sessionSelect.empty();
    for (const conversation of this.conversations) {
      this.sessionSelect.createEl("option", {
        value: conversation.id,
        text: conversation.title || "新会话"
      });
    }
    this.sessionSelect.value = this.activeConversationId;
  }

  private async send(): Promise<void> {
    const text = this.inputEl.value.trim();
    if (!text || !this.activeConversationId) return;
    const version = ++this.requestVersion;
    this.inputEl.value = "";
    this.approvals = [];
    this.messages.push({ id: crypto.randomUUID(), role: "user", text, createdAt: Date.now() });
    this.traces.push({ startedAt: Date.now(), tools: [] });
    this.setStatus("处理中…");
    this.renderAll();
    try {
      await streamConversation(
        this.app,
        this.agentPlugin.settings,
        this.activeConversationId,
        text,
        this.mode,
        this.queryScope,
        (event) => {
          if (version === this.requestVersion) this.handleEvent(event);
        }
      );
    } catch (error) {
      if (version !== this.requestVersion) return;
      this.messages.push({ id: crypto.randomUUID(), role: "error", text: errorText(error), createdAt: Date.now() });
      this.finishTrace();
      this.setStatus("请求失败", true);
      this.renderAll();
    }
  }

  private handleEvent(event: AgentEvent): void {
    const trace = this.traces[this.traces.length - 1];
    if (event.kind === "turn_started") {
      trace.submissionId = numberValue(event.data.submission_id);
      const mode = event.data.mode;
      if (mode === "default" || mode === "plan") this.mode = mode;
    } else if (event.kind === "assistant_message") {
      this.updateAssistantMessage(event);
    } else if (event.kind === "tool_call") {
      trace.tools.push({
        id: stringValue(event.data.call_id) || crypto.randomUUID(),
        name: event.text,
        arguments: event.data.arguments,
        state: "running"
      });
    } else if (event.kind === "tool_result") {
      const callId = stringValue(event.data.call_id);
      const tool = trace.tools.find((item) => item.id === callId);
      if (tool) tool.state = event.data.status === "interrupted" ? "interrupted" : event.data.is_error ? "error" : "success";
    } else if (event.kind === "approval_requested") {
      this.approvals.push({
        callId: stringValue(event.data.call_id),
        submissionId: numberValue(event.data.submission_id),
        name: stringValue(event.data.name),
        command: stringValue(event.data.command),
        justification: stringValue(event.data.justification)
      });
    } else if (event.kind === "plan_updated" && Array.isArray(event.data.plan)) {
      this.agentPlan = {
        explanation: event.text || undefined,
        plan: event.data.plan as AgentPlanState["plan"]
      };
    } else if (event.kind === "operation_plan" && isRecord(event.data.plan)) {
      this.operationPlan = toOperationPlan(event.data.plan);
      if (!this.operationPlan.requiresConfirmation) void this.executeCurrentPlan();
    } else if (event.kind === "error") {
      this.messages.push({ id: crypto.randomUUID(), role: "error", text: event.text, createdAt: Date.now() });
      this.finishTrace();
      this.setStatus("请求失败", true);
    } else if (["turn_finished", "turn_interrupted", "shutdown"].includes(event.kind)) {
      this.finishTrace();
      this.setStatus(event.kind === "turn_interrupted" ? "已中断" : "已连接");
      void this.refreshConversationSummaries();
    }
    this.renderAll();
  }

  private updateAssistantMessage(event: AgentEvent): void {
    let message = [...this.messages].reverse().find((item) => item.role === "assistant" && item.streaming);
    if (!message) {
      message = { id: crypto.randomUUID(), role: "assistant", text: "", createdAt: Date.now(), streaming: true };
      this.messages.push(message);
    }
    if (event.data.replace) message.text = event.text;
    else message.text += event.text;
    if (event.data.is_final) message.streaming = false;
  }

  private finishTrace(): void {
    const trace = this.traces[this.traces.length - 1];
    if (trace && !trace.completedAt) {
      trace.completedAt = Date.now();
      for (const tool of trace.tools) if (tool.state === "running") tool.state = "interrupted";
    }
    for (const message of this.messages) message.streaming = false;
  }

  private async refreshConversationSummaries(): Promise<void> {
    try {
      this.conversations = await listConversations(this.agentPlugin.settings);
      this.renderSessions();
    } catch { /* the active transcript remains usable */ }
  }

  private renderAll(): void {
    this.renderPlan();
    this.renderTimeline();
    this.renderApprovals();
    this.renderOperationPlan();
  }

  private renderPlan(): void {
    this.planEl.empty();
    if (!this.agentPlan) {
      this.planEl.hide();
      return;
    }
    this.planEl.show();
    const header = this.planEl.createDiv({ cls: "pka-panel-title", text: "Agent 工作计划" });
    const completed = this.agentPlan.plan.filter((item) => item.status === "completed").length;
    header.createSpan({ cls: "pka-plan-count", text: `${completed}/${this.agentPlan.plan.length}` });
    if (this.agentPlan.explanation) this.planEl.createDiv({ cls: "pka-plan-explanation", text: this.agentPlan.explanation });
    const list = this.planEl.createEl("ol");
    for (const item of this.agentPlan.plan) {
      list.createEl("li", { cls: `is-${item.status}`, text: item.step });
    }
  }

  private renderTimeline(): void {
    this.timelineEl.empty();
    const entries: Array<{ at: number; kind: "message" | "trace"; value: ViewMessage | RunTrace }> = [
      ...this.messages.map((value) => ({ at: value.createdAt, kind: "message" as const, value })),
      ...this.traces.map((value) => ({ at: value.startedAt, kind: "trace" as const, value }))
    ];
    entries.sort((left, right) => left.at - right.at);
    if (!entries.length) {
      this.timelineEl.createDiv({ cls: "pka-empty-state", text: "选择知识范围，然后开始对话。" });
      return;
    }
    for (const entry of entries) {
      if (entry.kind === "trace") this.renderTrace(entry.value as RunTrace);
      else void this.renderMessage(entry.value as ViewMessage);
    }
    this.timelineEl.scrollTop = this.timelineEl.scrollHeight;
  }

  private async renderMessage(message: ViewMessage): Promise<void> {
    const row = this.timelineEl.createDiv({ cls: `pka-message is-${message.role}` });
    const body = row.createDiv({ cls: "pka-message-body" });
    if (message.role === "assistant") {
      await MarkdownRenderer.render(this.app, message.text || "正在思考…", body, this.app.workspace.getActiveFile()?.path ?? "", this);
    } else {
      body.setText(message.text);
    }
  }

  private renderTrace(trace: RunTrace): void {
    const details = this.timelineEl.createEl("details", { cls: "pka-run-trace" });
    details.open = !trace.completedAt;
    const elapsed = Math.max(0, Math.round(((trace.completedAt ?? Date.now()) - trace.startedAt) / 1000));
    details.createEl("summary", { text: trace.completedAt ? `已处理 ${elapsed}s` : `正在处理 ${elapsed}s` });
    if (!trace.tools.length) {
      details.createDiv({ cls: "pka-tool-row", text: trace.completedAt ? "未调用工具" : "正在思考…" });
      return;
    }
    for (const tool of trace.tools) {
      const row = details.createDiv({ cls: `pka-tool-row is-${tool.state}` });
      row.createSpan({ cls: "pka-tool-state", text: tool.state === "running" ? "○" : tool.state === "success" ? "✓" : "!" });
      row.createEl("code", { text: tool.name });
      if (tool.arguments !== undefined) row.createEl("pre", { text: JSON.stringify(tool.arguments, null, 2) });
    }
  }

  private renderApprovals(): void {
    this.approvalEl.empty();
    for (const approval of this.approvals) {
      const card = this.approvalEl.createDiv({ cls: "pka-approval-card" });
      card.createDiv({ cls: "pka-panel-title", text: `权限请求：${approval.name}` });
      if (approval.command) card.createEl("code", { text: approval.command });
      if (approval.justification) card.createDiv({ text: approval.justification });
      const actions = card.createDiv({ cls: "pka-panel-actions" });
      this.actionButton(actions, "拒绝", () => void this.answerApproval(approval, false));
      this.actionButton(actions, "允许一次", () => void this.answerApproval(approval, true), true);
    }
  }

  private async answerApproval(approval: ApprovalRequest, approved: boolean): Promise<void> {
    if (approval.resolving) return;
    approval.resolving = true;
    try {
      await resolveApproval(
        this.agentPlugin.settings,
        this.activeConversationId,
        approval.callId,
        approval.submissionId,
        approved
      );
      this.approvals = this.approvals.filter((item) => item !== approval);
      this.renderApprovals();
    } catch (error) {
      new Notice(errorText(error));
      approval.resolving = false;
    }
  }

  private renderOperationPlan(): void {
    this.operationEl.empty();
    const plan = this.operationPlan;
    if (!plan) {
      this.operationEl.hide();
      return;
    }
    this.operationEl.show();
    const header = this.operationEl.createDiv({ cls: "pka-panel-title", text: "Vault 修改预览" });
    header.createSpan({ cls: `pka-risk is-${plan.risk}`, text: riskText(plan.risk) });
    this.operationEl.createDiv({ cls: "pka-operation-summary", text: plan.summary });
    const list = this.operationEl.createEl("ol");
    for (const operation of plan.operations) list.createEl("li", { text: describeOperation(operation) });
    if (plan.status) this.operationEl.createDiv({ cls: "pka-operation-status", text: `状态：${plan.status}` });
    const actions = this.operationEl.createDiv({ cls: "pka-panel-actions" });
    if (!plan.status || plan.status === "pending") {
      this.actionButton(actions, plan.requiresConfirmation ? "确认执行" : "正在自动执行", () => void this.executeCurrentPlan(), true, !plan.requiresConfirmation);
    }
    if (plan.status === "succeeded" && plan.rollbackToken) {
      this.actionButton(actions, "撤销", () => void this.rollbackCurrentPlan());
    }
  }

  private actionButton(
    parent: HTMLElement,
    text: string,
    action: () => void,
    primary = false,
    disabled = false
  ): void {
    const button = parent.createEl("button", { cls: primary ? "mod-cta" : "", text });
    button.disabled = disabled;
    button.onclick = action;
  }

  private async executeCurrentPlan(): Promise<void> {
    const plan = this.operationPlan;
    if (!plan || plan.status === "running" || plan.status === "succeeded") return;
    plan.status = "running";
    this.renderOperationPlan();
    try {
      const results = await this.agentPlugin.executePlan(plan);
      plan.status = "succeeded";
      this.messages.push({ id: crypto.randomUUID(), role: "notice", text: results.join("\n"), createdAt: Date.now() });
    } catch (error) {
      plan.status = "failed";
      this.messages.push({ id: crypto.randomUUID(), role: "error", text: errorText(error), createdAt: Date.now() });
    }
    this.renderAll();
  }

  private async rollbackCurrentPlan(): Promise<void> {
    const plan = this.operationPlan;
    if (!plan) return;
    try {
      const results = await this.agentPlugin.rollbackPlan(plan);
      plan.status = "rolled_back";
      this.messages.push({ id: crypto.randomUUID(), role: "notice", text: results.join("\n"), createdAt: Date.now() });
    } catch (error) {
      this.messages.push({ id: crypto.randomUUID(), role: "error", text: errorText(error), createdAt: Date.now() });
    }
    this.renderAll();
  }

  private setStatus(text: string, error = false): void {
    this.statusEl.setText(text);
    this.statusEl.toggleClass("is-error", error);
  }
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "发生未知错误。";
}

function riskText(risk: OperationPlan["risk"]): string {
  return risk === "low" ? "低风险" : risk === "medium" ? "中风险" : "高风险";
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function numberValue(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
