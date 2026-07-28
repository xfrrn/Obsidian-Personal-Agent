const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);

export function normalizeAgentUrl(value: string): string {
  let url: URL;
  try {
    url = new URL(value.trim());
  } catch {
    throw new Error("CodeX-Agent 地址必须是有效的 HTTP 或 HTTPS 本机地址。");
  }
  if (!["http:", "https:"].includes(url.protocol)) {
    throw new Error("CodeX-Agent 地址必须使用 HTTP 或 HTTPS。");
  }
  if (url.username || url.password || !LOOPBACK_HOSTS.has(url.hostname.toLowerCase())) {
    throw new Error("CodeX-Agent 地址必须指向本机回环地址。");
  }
  return url.origin;
}

export function agentPortFromUrl(value: string): string {
  const url = new URL(normalizeAgentUrl(value));
  return url.port || (url.protocol === "https:" ? "443" : "80");
}

export function normalizeApiBaseUrl(value: string): string {
  const url = new URL(value.trim());
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) {
    throw new Error("模型地址必须是无内嵌凭据的 HTTP 或 HTTPS 地址。");
  }
  return url.toString().replace(/\/$/, "");
}
