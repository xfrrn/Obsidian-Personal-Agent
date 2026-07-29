export function webFetchMethodSummary(value: unknown) {
  if (!Array.isArray(value)) return ""
  let local = 0
  const providers = new Map<string, number>()
  for (const source of value) {
    if (typeof source !== "object" || source === null || Array.isArray(source)) continue
    const method = typeof source.fetch_method === "string" ? source.fetch_method : ""
    if (method === "local" && source.fallback_used !== true) {
      local += 1
      continue
    }
    if (source.fallback_used === true || method === "tavily" || method === "exa") {
      const provider = method === "tavily" ? "Tavily" : method === "exa" ? "Exa" : "Provider"
      providers.set(provider, (providers.get(provider) || 0) + 1)
    }
  }
  const parts = [
    ...(local ? [["本地静态抓取", local] as const] : []),
    ...[...providers].map(([provider, count]) => [`${provider} 回退`, count] as const),
  ]
  if (!parts.length) return ""
  return parts.map(([label, count]) => parts.length > 1 ? `${label} ${count}` : label).join(" · ")
}
