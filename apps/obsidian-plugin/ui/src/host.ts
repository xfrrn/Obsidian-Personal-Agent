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

export type HostSettingsPatch = { sandboxMode?: SandboxMode; themeMode?: ThemeMode }

export type ThemeToken =
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

const styleNames = [
  "--background", "--card", "--page", "--muted", "--accent", "--border", "--input",
  "--foreground", "--card-foreground", "--muted-foreground", "--primary",
  "--primary-foreground", "--error", "--success", "--font-interface", "--font-text",
]

/** 把 Obsidian 主题令牌限制在 Agent 根节点，避免污染整个应用。 */
export function applyThemeTokens(element: HTMLElement, tokens: HostState["theme"]["tokens"]): void {
  const style = element.style
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

export function clearThemeTokens(element: HTMLElement): void {
  for (const name of styleNames) element.style.removeProperty(name)
}
