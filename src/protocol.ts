export interface AgentCitation {
  path: string;
  heading?: string;
}

export interface AgentAnswer {
  answer: string;
  citations: AgentCitation[];
}

export class AgentError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AgentError";
  }
}

export function chatCompletionsUrl(baseUrl: string): string {
  let url: URL;
  try {
    url = new URL(baseUrl);
  } catch {
    throw new AgentError("API Base URL 无效。请在插件设置中检查地址。");
  }

  const localHost = ["localhost", "127.0.0.1", "::1", "[::1]"].includes(url.hostname);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && localHost)) {
    throw new AgentError("API 地址必须使用 HTTPS；普通 HTTP 只允许 localhost。");
  }
  if (url.username || url.password) {
    throw new AgentError("API 地址不能包含用户名或密码。");
  }

  const path = url.pathname.replace(/\/+$/, "");
  url.pathname = path.endsWith("/chat/completions")
    ? path
    : `${path}/chat/completions`;
  url.search = "";
  url.hash = "";
  return url.toString();
}

export function extractChatContent(payload: unknown): string {
  if (!isRecord(payload) || !Array.isArray(payload.choices)) {
    throw new AgentError("模型服务返回了无法识别的响应。");
  }
  const choice = payload.choices[0];
  if (!isRecord(choice) || !isRecord(choice.message)) {
    throw new AgentError("模型服务没有返回回答内容。");
  }
  const content = choice.message.content;
  if (typeof content !== "string" || !content.trim()) {
    throw new AgentError("模型服务返回了空回答。");
  }
  return content;
}

export function parseCandidatePaths(
  text: string,
  allowedPaths: ReadonlySet<string>,
  limit: number
): string[] {
  const value = parseJsonObject(text);
  if (!Array.isArray(value.paths)) {
    throw new AgentError("模型没有按要求返回候选笔记路径。");
  }

  const result: string[] = [];
  for (const path of value.paths) {
    if (
      typeof path === "string" &&
      allowedPaths.has(path) &&
      !result.includes(path)
    ) {
      result.push(path);
      if (result.length >= limit) break;
    }
  }
  return result;
}

export function parseAgentAnswer(
  text: string,
  allowedPaths: ReadonlySet<string>,
  allowedHeadings: ReadonlyMap<string, ReadonlySet<string>> = new Map()
): AgentAnswer {
  const value = parseJsonObject(text);
  if (typeof value.answer !== "string" || !value.answer.trim()) {
    throw new AgentError("模型没有按要求返回回答正文。");
  }
  if (!Array.isArray(value.citations)) {
    throw new AgentError("模型没有按要求返回引用列表。");
  }

  const citations: AgentCitation[] = [];
  for (const raw of value.citations) {
    if (!isRecord(raw) || typeof raw.path !== "string") continue;
    if (!allowedPaths.has(raw.path)) continue;
    const requestedHeading = typeof raw.heading === "string"
      ? raw.heading.trim()
      : "";
    const heading = requestedHeading && allowedHeadings.get(raw.path)?.has(requestedHeading)
      ? requestedHeading
      : undefined;
    if (!citations.some((item) => item.path === raw.path && item.heading === heading)) {
      citations.push({ path: raw.path, heading });
    }
  }

  return { answer: value.answer.trim(), citations };
}

export function parseJsonObject(text: string): Record<string, unknown> {
  const trimmed = text.trim();
  const withoutFence = trimmed
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/\s*```$/, "");
  const start = withoutFence.indexOf("{");
  const end = withoutFence.lastIndexOf("}");
  if (start < 0 || end <= start) {
    throw new AgentError("模型返回的内容不是有效 JSON。");
  }

  try {
    const value: unknown = JSON.parse(withoutFence.slice(start, end + 1));
    if (!isRecord(value)) throw new Error("not an object");
    return value;
  } catch {
    throw new AgentError("模型返回的 JSON 无法解析，请重试。");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
