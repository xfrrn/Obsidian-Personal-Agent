import { ItemView, requestUrl, WorkspaceLeaf } from "obsidian";
import { normalizeAgentUrl } from "./url";
import { HOST_MESSAGE_SOURCE, parsePanelMessage, ThemeMode } from "./bridge";
import type { SandboxMode } from "./settings";

export const CODEX_AGENT_VIEW_TYPE = "codex-agent-view";

export class CodeXAgentView extends ItemView {
  private frame: HTMLIFrameElement | null = null;

  constructor(
    leaf: WorkspaceLeaf,
    private readonly getAgentUrl: () => string,
    private readonly syncAgentSettings: () => Promise<void>,
    private readonly getPanelSettings: () => { sandboxMode: SandboxMode; themeMode: ThemeMode },
    private readonly persistPanelSettings: (settings: { sandboxMode?: SandboxMode; themeMode?: ThemeMode }) => Promise<void>
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
    this.registerDomEvent(window, "message", (event) => void this.handleMessage(event));
    this.registerEvent(this.app.workspace.on("css-change", () => this.postHostState()));
    this.registerEvent(this.app.workspace.on("file-open", () => this.postHostState()));
    await this.refresh();
  }

  async refresh(): Promise<void> {
    const content = this.containerEl.children[1] as HTMLElement;
    content.empty();
    content.addClass("codex-agent-view");

    try {
      const agentUrl = normalizeAgentUrl(this.getAgentUrl());
      const response = await requestUrl({ url: `${agentUrl}/api/config`, method: "GET", throw: false });
      if (response.status < 200 || response.status >= 300) throw new Error(`HTTP ${response.status}`);
      await this.syncAgentSettings().catch(() => undefined);
      const frame = content.createEl("iframe", { cls: "codex-agent-frame" });
      this.frame = frame;
      frame.src = agentUrl;
      frame.title = "CodeX-Agent";
      frame.allow = "clipboard-read; clipboard-write";
      frame.addEventListener("load", () => this.postHostState(), { once: true });
    } catch (error) {
      this.frame = null;
      const disconnected = content.createDiv({ cls: "codex-agent-disconnected" });
      disconnected.createEl("strong", { text: "无法连接本地 Agent" });
      disconnected.createEl("span", { text: error instanceof Error ? error.message : "请确认服务已经启动。" });
      disconnected.createEl("button", { text: "重新连接" }).addEventListener("click", () => void this.refresh());
    }
  }

  private async handleMessage(event: MessageEvent): Promise<void> {
    if (!this.frame?.contentWindow || event.source !== this.frame.contentWindow) return;
    if (event.origin !== new URL(normalizeAgentUrl(this.getAgentUrl())).origin) return;
    const message = parsePanelMessage(event.data);
    if (!message) return;
    if (message.type === "settings:update") await this.persistPanelSettings(message.settings);
    this.postHostState();
  }

  private postHostState(): void {
    const target = this.frame?.contentWindow;
    if (!target) return;
    const style = getComputedStyle(document.body);
    const token = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
    const adapter = this.app.vault.adapter as { getBasePath?: () => string };
    const settings = this.getPanelSettings();
    target.postMessage({
      source: HOST_MESSAGE_SOURCE,
      type: "host:state",
      theme: {
        mode: settings.themeMode,
        isDark: document.body.classList.contains("theme-dark"),
        tokens: {
          backgroundPrimary: token("--background-primary", "#ffffff"),
          backgroundSecondary: token("--background-secondary", "#f6f6f6"),
          backgroundHover: token("--background-modifier-hover", "rgba(0, 0, 0, 0.075)"),
          border: token("--background-modifier-border", "#dddddd"),
          textNormal: token("--text-normal", "#222222"),
          textMuted: token("--text-muted", "#666666"),
          textAccent: token("--text-accent", "#7f6df2"),
          textOnAccent: token("--text-on-accent", "#ffffff"),
          textError: token("--text-error", "#d14b4b"),
          textSuccess: token("--text-success", "#2d9d5b"),
          fontInterface: token("--font-interface-theme", "system-ui"),
          fontText: token("--font-text-theme", "system-ui")
        }
      },
      context: {
        workspace: adapter.getBasePath?.() ?? "",
        activeFile: this.app.workspace.getActiveFile()?.path ?? null
      },
      settings: { sandboxMode: settings.sandboxMode }
    }, new URL(normalizeAgentUrl(this.getAgentUrl())).origin);
  }
}
