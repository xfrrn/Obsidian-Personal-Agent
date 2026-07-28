import { ItemView, WorkspaceLeaf } from "obsidian";
import { mountAgentApp, type MountedAgentApp } from "../ui/src/mount";
import type { AgentAppProps } from "../ui/src/App";
import { normalizeAgentUrl } from "./url";
import type { SandboxMode } from "./settings";
import type { ThemeMode } from "./theme";

export const CODEX_AGENT_VIEW_TYPE = "codex-agent-view";

export class CodeXAgentView extends ItemView {
  private agentApp: MountedAgentApp | null = null;

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
    this.registerEvent(this.app.workspace.on("css-change", () => this.renderHostState()));
    this.registerEvent(this.app.workspace.on("file-open", () => this.renderHostState()));
    await this.refresh();
  }

  async onClose(): Promise<void> {
    this.agentApp?.unmount();
    this.agentApp = null;
  }

  /** 启动后端并把 React 直接挂到 ItemView。 */
  async refresh(): Promise<void> {
    const content = this.containerEl.children[1] as HTMLElement;
    this.agentApp?.unmount();
    this.agentApp = null;
    content.empty();
    content.addClass("codex-agent-view");

    try {
      await this.syncAgentSettings();
      const root = content.createDiv({ cls: "codex-agent-ui" });
      this.agentApp = mountAgentApp(root, this.appProps());
    } catch (error) {
      const disconnected = content.createDiv({ cls: "codex-agent-disconnected" });
      disconnected.createEl("strong", { text: "无法连接本地 Agent" });
      disconnected.createEl("span", { text: error instanceof Error ? error.message : "请确认服务已经启动。" });
      disconnected.createEl("button", { text: "重新连接" }).addEventListener("click", () => void this.refresh());
    }
  }

  private renderHostState(): void {
    this.agentApp?.render(this.appProps());
  }

  private appProps(): AgentAppProps {
    const style = getComputedStyle(document.body);
    const token = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
    const adapter = this.app.vault.adapter as { getBasePath?: () => string };
    const settings = this.getPanelSettings();
    return {
      agentUrl: normalizeAgentUrl(this.getAgentUrl()),
      hostState: {
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
      },
      onSettingsChange: async (next) => {
        await this.persistPanelSettings(next);
        this.renderHostState();
      }
    };
  }
}
