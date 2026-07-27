export type ThemeMode = "system" | "light" | "dark"
export type SandboxMode = "read-only" | "workspace-write" | "danger-full-access"

export type HostState = {
  theme: {
    mode: ThemeMode
    isDark: boolean
    tokens: Record<ThemeToken, string>
  }
  context: { workspace: string; activeFile: string | null }
  settings: { sandboxMode: SandboxMode }
}

type ThemeToken =
  | "backgroundPrimary"
  | "backgroundSecondary"
  | "backgroundHover"
  | "border"
  | "textNormal"
  | "textMuted"
  | "textAccent"
  | "textOnAccent"
  | "textError"
  | "textSuccess"
  | "fontInterface"
  | "fontText"

const HOST_SOURCE = "obsidian-agent-plugin"
const PANEL_SOURCE = "codex-agent-ui"
const tokenNames: ThemeToken[] = [
  "backgroundPrimary", "backgroundSecondary", "backgroundHover", "border",
  "textNormal", "textMuted", "textAccent", "textOnAccent", "textError",
  "textSuccess", "fontInterface", "fontText",
]

export function subscribeToObsidian(onState: (state: HostState) => void): () => void {
  const listener = (event: MessageEvent) => {
    if (event.source !== window.parent) return
    const state = parseHostState(event.data)
    if (state) onState(state)
  }
  window.addEventListener("message", listener)
  window.parent.postMessage({ source: PANEL_SOURCE, type: "ready" }, "*")
  return () => window.removeEventListener("message", listener)
}

export function updateObsidianSettings(settings: { sandboxMode?: SandboxMode; themeMode?: ThemeMode }): void {
  window.parent.postMessage({ source: PANEL_SOURCE, type: "settings:update", settings }, "*")
}

export function applyThemeTokens(tokens: HostState["theme"]["tokens"]): void {
  const style = document.documentElement.style
  style.setProperty("--background", tokens.backgroundPrimary)
  style.setProperty("--card", tokens.backgroundPrimary)
  style.setProperty("--page", tokens.backgroundSecondary)
  style.setProperty("--muted", tokens.backgroundSecondary)
  style.setProperty("--accent", tokens.backgroundHover)
  style.setProperty("--border", tokens.border)
  style.setProperty("--input", tokens.border)
  style.setProperty("--foreground", tokens.textNormal)
  style.setProperty("--card-foreground", tokens.textNormal)
  style.setProperty("--muted-foreground", tokens.textMuted)
  style.setProperty("--primary", tokens.textAccent)
  style.setProperty("--primary-foreground", tokens.textOnAccent)
  style.setProperty("--error", tokens.textError)
  style.setProperty("--success", tokens.textSuccess)
  style.setProperty("--font-interface", tokens.fontInterface)
  style.setProperty("--font-text", tokens.fontText)
}

export function clearThemeTokens(): void {
  const style = document.documentElement.style
  for (const name of [
    "--background", "--card", "--page", "--muted", "--accent", "--border", "--input",
    "--foreground", "--card-foreground", "--muted-foreground", "--primary",
    "--primary-foreground", "--error", "--success", "--font-interface", "--font-text",
  ]) style.removeProperty(name)
}

export function parseHostState(value: unknown): HostState | null {
  if (!isRecord(value) || value.source !== HOST_SOURCE || value.type !== "host:state") return null
  if (!isRecord(value.theme) || !isRecord(value.context) || !isRecord(value.settings)) return null
  const theme = value.theme
  if (!isRecord(theme.tokens)) return null
  const tokens = theme.tokens
  if (!isThemeMode(theme.mode) || typeof theme.isDark !== "boolean") return null
  if (typeof value.context.workspace !== "string" || !(typeof value.context.activeFile === "string" || value.context.activeFile === null)) return null
  if (!isSandboxMode(value.settings.sandboxMode)) return null
  if (tokenNames.some((name) => typeof tokens[name] !== "string")) return null

  return {
    theme: {
      mode: theme.mode,
      isDark: theme.isDark,
      tokens: Object.fromEntries(tokenNames.map((name) => [name, tokens[name]])) as Record<ThemeToken, string>,
    },
    context: { workspace: value.context.workspace, activeFile: value.context.activeFile },
    settings: { sandboxMode: value.settings.sandboxMode },
  }
}

function isThemeMode(value: unknown): value is ThemeMode {
  return value === "system" || value === "light" || value === "dark"
}

function isSandboxMode(value: unknown): value is SandboxMode {
  return value === "read-only" || value === "workspace-write" || value === "danger-full-access"
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}
