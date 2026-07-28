export type FileSuggestionKey = "ArrowUp" | "ArrowDown" | "Enter"

export function fileSuggestionIndex(
  current: number,
  count: number,
  key: FileSuggestionKey,
): number | null {
  if (count === 0) return null
  const safe = Math.min(current, count - 1)
  if (key === "Enter") return safe
  return (safe + (key === "ArrowDown" ? 1 : -1) + count) % count
}
