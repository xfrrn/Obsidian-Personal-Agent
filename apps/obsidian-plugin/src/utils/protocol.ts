export interface AgentCitation {
  path: string;
  heading?: string;
}

export interface AgentAnswer {
  answer: string;
  citations: AgentCitation[];
  trace?: AgentTraceStep[];
}

export interface AgentTraceStep {
  round: number;
  toolName: string;
  status: string;
  summary: string;
  detail?: Record<string, unknown>;
}

export type AgentIntent = "ask" | "plan";

export function inferIntent(input: string): AgentIntent {
  const text = input.trim();
  if (isTaskCompletionRequest(text)) return "plan";
  if (/^(如何|怎么|怎样|为什么|解释|介绍|总结|概括|查询|搜索|查找)/.test(text)) return "ask";
  return /(?:创建|新建|修改|更新|编辑|移动|归档|追加|添加|删除).{0,12}(?:笔记|元数据|frontmatter|标签|任务)|(?:笔记|元数据|frontmatter|标签|任务).{0,12}(?:创建|新建|修改|更新|编辑|移动|归档|追加|添加|删除)/i.test(text)
    ? "plan"
    : "ask";
}

export function isTaskCompletionRequest(input: string): boolean {
  return /(?:标记|设为|改为|置为|打勾).{0,12}完成|^(?:帮我)?完成(?:一下)?(?:任务|待办)/i.test(input);
}

export function isTaskQuery(input: string): boolean {
  return /待办|任务|todo|行动项|未完成事项|已完成事项/i.test(input);
}

export function isLocalAnalysisQuery(input: string): boolean {
  return /(?:检查|分析).{0,12}(?:当前笔记|笔记规范|项目|知识库)|知识库.{0,8}(?:健康|体检)|(?:列出|统计|查看).{0,8}标签|(?:关联|相关|重复|相似).{0,8}笔记|(?:列出|查看|试运行|评估).{0,8}规则|(?:提取|找出).{0,12}(?:潜在任务|任务候选)/i.test(input);
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

export function parseIntent(text: string): AgentIntent {
  const value = parseJsonObject(text);
  if (value.intent === "ask" || value.intent === "plan") return value.intent;
  throw new AgentError("模型没有按要求返回意图判断。");
}

export function parseJsonObject(text: string): Record<string, unknown> {
  const trimmed = text.trim();
  const fenced = /^```(?:json)?\s*([\s\S]*?)\s*```$/i.exec(trimmed);
  const json = (fenced?.[1] ?? trimmed).trim();
  if (!json.startsWith("{") || !json.endsWith("}")) {
    throw new AgentError("模型返回的内容不是有效 JSON。");
  }

  try {
    const value: unknown = JSON.parse(json);
    if (!isRecord(value)) throw new Error("not an object");
    return value;
  } catch {
    throw new AgentError("模型返回的 JSON 无法解析，请重试。");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
