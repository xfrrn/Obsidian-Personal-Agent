export interface SearchCatalogItem {
  path: string;
  title: string;
  type?: string;
  project?: string;
  status?: string;
  tags: string[];
  headings: string[];
  excerpt?: string;
}

export function rankCandidateNotes(
  items: readonly SearchCatalogItem[],
  query: string,
  limit: number
): SearchCatalogItem[] {
  if (items.length <= limit) return [...items];

  const normalizedQuery = normalize(query);
  const tokens = tokenize(normalizedQuery);
  const ranked = items
    .map((item, index) => ({ item, index, score: scoreItem(item, normalizedQuery, tokens) }))
    .sort((a, b) => b.score - a.score || a.index - b.index);

  const hits = ranked.filter((entry) => entry.score > 0);
  return (hits.length ? hits : ranked).slice(0, limit).map((entry) => entry.item);
}

function scoreItem(
  item: SearchCatalogItem,
  query: string,
  tokens: readonly string[]
): number {
  return scoreField(item.title, query, tokens, 20) +
    scoreField(item.path, query, tokens, 14) +
    scoreField(item.tags.join(" "), query, tokens, 12) +
    scoreField(item.project, query, tokens, 10) +
    scoreField(item.type, query, tokens, 8) +
    scoreField(item.status, query, tokens, 6) +
    scoreField(item.headings.join(" "), query, tokens, 8) +
    scoreField(item.excerpt, query, tokens, 4);
}

function scoreField(
  value: string | undefined,
  query: string,
  tokens: readonly string[],
  weight: number
): number {
  const text = normalize(value ?? "");
  if (!text) return 0;

  let score = query && text.includes(query) ? weight * 3 : 0;
  for (const token of tokens) {
    if (text.includes(token)) score += weight;
  }
  return score;
}

function tokenize(text: string): string[] {
  return [...new Set(text.match(/[a-z0-9]+|[\u4e00-\u9fff]{2,}/g) ?? [])];
}

function normalize(text: string): string {
  return text.toLowerCase().replace(/\s+/g, " ").trim();
}
