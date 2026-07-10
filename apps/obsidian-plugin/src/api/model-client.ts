import { App, requestUrl } from "obsidian";
import type { ChatMessage } from "../features/assistant/types";
import {
  AgentError,
  chatCompletionsUrl,
  extractChatContent
} from "../utils/protocol";
import type { AgentSettings } from "../settings/settings";

export async function callModel(
  app: App,
  settings: AgentSettings,
  messages: ChatMessage[]
): Promise<string> {
  const model = settings.model.trim();
  if (!model) throw new AgentError("请先在插件设置中填写模型名称。");

  const secret = settings.secretId
    ? app.secretStorage.getSecret(settings.secretId)
    : null;
  if (settings.secretId && !secret) {
    throw new AgentError("选中的 API 密钥不存在，请重新选择。");
  }

  const headers: Record<string, string> = {
    "Content-Type": "application/json"
  };
  if (secret) headers.Authorization = `Bearer ${secret}`;

  try {
    const response = await requestUrl({
      url: chatCompletionsUrl(settings.apiBaseUrl),
      method: "POST",
      headers,
      body: JSON.stringify({ model, messages }),
      throw: false
    });
    if (response.status < 200 || response.status >= 300) {
      throw new AgentError(`模型服务请求失败（HTTP ${response.status}）。`);
    }
    return extractChatContent(response.json as unknown);
  } catch (error) {
    if (error instanceof AgentError) throw error;
    throw new AgentError("无法连接模型服务，请检查地址、网络和密钥。");
  }
}
