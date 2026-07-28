export interface AgentLaunchSpec {
  command: string;
  args: string[];
}

/** 把本机 Agent 地址转换为内置 EXE 或开发环境 Python 的启动参数。 */
export function agentLaunchSpec(agentUrl: string, executable = ""): AgentLaunchSpec {
  const url = new URL(agentUrl);
  if (url.protocol !== "http:") {
    throw new Error("自动启动本地 Agent 只支持 HTTP 地址。");
  }
  if (url.hostname === "[::1]") {
    throw new Error("自动启动本地 Agent 暂不支持 IPv6 地址。");
  }
  const host = url.hostname === "localhost" ? "127.0.0.1" : url.hostname;
  return {
    command: executable || "python",
    args: [
      ...(executable ? [] : ["-m", "agent.web.server"]),
      "--host", host, "--port", url.port || "80"
    ]
  };
}
