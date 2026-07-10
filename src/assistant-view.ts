import {
  ItemView,
  MarkdownRenderer,
  WorkspaceLeaf
} from "obsidian";
import type PersonalKnowledgeAgentPlugin from "./main";
import type { QueryScope } from "./agent";
import {
  describeOperation,
  OperationPlan
} from "./operations";
import { AgentAnswer, AgentError } from "./protocol";

export const AGENT_VIEW_TYPE = "personal-knowledge-agent-view";

export class AssistantView extends ItemView {
  private questionEl!: HTMLTextAreaElement;
  private modeEl!: HTMLSelectElement;
  private scopeEl!: HTMLSelectElement;
  private sendButton!: HTMLButtonElement;
  private resultEl!: HTMLElement;
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
    contentEl.createEl("h2", { text: "个人知识库 Agent" });

    const modeRow = contentEl.createDiv({ cls: "pka-field" });
    modeRow.createEl("label", {
      text: "模式",
      attr: { for: "pka-mode" }
    });
    this.modeEl = modeRow.createEl("select", {
      attr: { id: "pka-mode" }
    });
    this.modeEl.createEl("option", { text: "问答", value: "ask" });
    this.modeEl.createEl("option", { text: "修改计划", value: "plan" });

    const scopeRow = contentEl.createDiv({ cls: "pka-field" });
    scopeRow.createEl("label", {
      text: "查询范围",
      attr: { for: "pka-query-scope" }
    });
    this.scopeEl = scopeRow.createEl("select", {
      attr: { id: "pka-query-scope" }
    });
    this.scopeEl.createEl("option", { text: "当前笔记", value: "current" });
    this.scopeEl.createEl("option", { text: "整个知识库", value: "vault" });

    const questionRow = contentEl.createDiv({ cls: "pka-field pka-grow" });
    questionRow.createEl("label", {
      text: "问题",
      attr: { for: "pka-question" }
    });
    this.questionEl = questionRow.createEl("textarea", {
      cls: "pka-question",
      attr: {
        id: "pka-question",
        rows: "5",
        placeholder: "询问当前笔记或整个知识库……"
      }
    });

    this.sendButton = contentEl.createEl("button", {
      cls: "mod-cta pka-send",
      text: "发送"
    });
    this.resultEl = contentEl.createDiv({
      cls: "pka-result",
      attr: { "aria-live": "polite" }
    });
    this.resultEl.setText("输入问题或修改请求后按 Ctrl/Cmd + Enter 发送。");

    this.registerDomEvent(this.sendButton, "click", () => void this.submit());
    this.registerDomEvent(this.questionEl, "keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
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
    this.setBusy(true);
    this.resultEl.empty();
    this.resultEl.setText(this.modeEl.value === "plan"
      ? "正在生成修改计划……"
      : "正在查找相关笔记……");

    try {
      if (this.modeEl.value === "plan") {
        this.renderPlan(await this.agentPlugin.plan(
          this.questionEl.value,
          this.scopeEl.value as QueryScope
        ));
      } else {
        const answer = await this.agentPlugin.ask(
          this.questionEl.value,
          this.scopeEl.value as QueryScope
        );
        await this.renderAnswer(answer);
      }
    } catch (error) {
      this.resultEl.empty();
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
    this.resultEl.empty();
    const answerEl = this.resultEl.createDiv({ cls: "pka-answer markdown-rendered" });
    await MarkdownRenderer.render(
      this.app,
      answer.answer,
      answerEl,
      answer.citations[0]?.path ?? "",
      this
    );

    if (!answer.citations.length) return;
    const citationsEl = this.resultEl.createDiv({ cls: "pka-citations" });
    citationsEl.createEl("h3", { text: "引用" });
    const list = citationsEl.createEl("ul");

    for (const citation of answer.citations) {
      const item = list.createEl("li");
      const label = citation.heading
        ? `${citation.path} › ${citation.heading}`
        : citation.path;
      const button = item.createEl("button", {
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
    this.resultEl.empty();
    this.resultEl.createEl("h3", { text: "修改预览" });
    this.resultEl.createEl("p", { text: `${plan.summary}（风险：${plan.risk}）` });

    const list = this.resultEl.createEl("ol", { cls: "pka-plan" });
    for (const operation of plan.operations) {
      list.createEl("li", { text: describeOperation(operation) });
    }

    const executeButton = this.resultEl.createEl("button", {
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
    executeButton.setText("执行中……");

    try {
      const results = await this.agentPlugin.executePlan(plan);
      this.resultEl.empty();
      this.resultEl.createEl("h3", { text: "已执行" });
      const list = this.resultEl.createEl("ul", { cls: "pka-plan" });
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
    this.sendButton.setText(busy ? "处理中……" : "发送");
  }
}
