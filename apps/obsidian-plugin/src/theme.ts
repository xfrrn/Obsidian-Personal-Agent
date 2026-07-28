export type ThemeMode = "system" | "light" | "dark";

export function isThemeMode(value: unknown): value is ThemeMode {
  return value === "system" || value === "light" || value === "dark";
}
