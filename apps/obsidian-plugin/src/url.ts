const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);

export function normalizeAgentUrl(value: string): string {
  const url = new URL(value.trim());
  if (!["http:", "https:"].includes(url.protocol)) {
    throw new Error("CodeX-Agent 地址必须使用 HTTP 或 HTTPS。");
  }
  if (url.username || url.password || !LOOPBACK_HOSTS.has(url.hostname.toLowerCase())) {
    throw new Error("CodeX-Agent 地址必须指向本机回环地址。");
  }
  return url.origin;
}
