import type { SandboxMode } from "./settings";

export const PANEL_MESSAGE_SOURCE = "codex-agent-ui";
export const HOST_MESSAGE_SOURCE = "obsidian-agent-plugin";

export type ThemeMode = "system" | "light" | "dark";

export type PanelMessage =
  | { type: "ready" }
  | { type: "settings:update"; settings: { sandboxMode?: SandboxMode; themeMode?: ThemeMode } };

export function parsePanelMessage(value: unknown): PanelMessage | null {
  if (!isRecord(value) || value.source !== PANEL_MESSAGE_SOURCE) return null;
  if (value.type === "ready") return { type: "ready" };
  if (value.type !== "settings:update" || !isRecord(value.settings)) return null;
  if (Object.keys(value.settings).some((key) => !["sandboxMode", "themeMode"].includes(key))) return null;

  const settings: { sandboxMode?: SandboxMode; themeMode?: ThemeMode } = {};
  if (value.settings.sandboxMode !== undefined) {
    if (!isSandboxMode(value.settings.sandboxMode)) return null;
    settings.sandboxMode = value.settings.sandboxMode;
  }
  if (value.settings.themeMode !== undefined) {
    if (!isThemeMode(value.settings.themeMode)) return null;
    settings.themeMode = value.settings.themeMode;
  }
  return Object.keys(settings).length ? { type: "settings:update", settings } : null;
}

export function isThemeMode(value: unknown): value is ThemeMode {
  return value === "system" || value === "light" || value === "dark";
}

function isSandboxMode(value: unknown): value is SandboxMode {
  return value === "read-only" || value === "workspace-write" || value === "danger-full-access";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
