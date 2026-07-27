"""一个 Agent Session 对应的异步 OpenAI Chat Completions HTTP 会话。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

from agent.config.settings import Settings
from agent.llm.types import (
    AssistantResponse,
    ClientError,
    ContextLimitError,
    TokenUsage,
    ToolCall,
)


TextDeltaHandler = Callable[[str], Awaitable[None]]


class ModelClientSession:
    """执行模型请求，并在流式请求被取消时关闭其 HTTP 响应连接。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AssistantResponse:
        """执行非流式请求，供不需要逐字展示的上下文压缩使用。"""

        response_data = await self._post_json(self._payload(messages, tools))
        return _parse_response(response_data)

    async def stream_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text_delta: TextDeltaHandler,
    ) -> AssistantResponse:
        """流式读取文本，并在流结束后组合出完整的工具调用和 usage。"""

        payload = self._payload(messages, tools)
        payload["stream"] = True
        # OpenAI 在最后一个 SSE chunk 中携带 usage；兼容端点忽略它时仍可正常流式工作。
        payload["stream_options"] = {"include_usage": True}
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_call_parts: dict[int, dict[str, Any]] = {}
        usage: TokenUsage | None = None
        end_turn: bool | None = None

        try:
            async with httpx.AsyncClient(timeout=self._settings.request_timeout_seconds) as client:
                # async with 在 CancelledError 向上传播时也会关闭 response/client，
                # 因而不再像 to_thread 那样留下继续读取网络的后台线程。
                async with client.stream(
                    "POST", self._endpoint, headers=self._headers, json=payload
                ) as response:
                    if response.is_error:
                        detail = (await response.aread()).decode("utf-8", errors="replace")[:1000]
                        raise _http_error(response.status_code, detail)
                    async for chunk_data in _sse_chunks(response):
                        if chunk_data == "[DONE]":
                            break
                        chunk = _parse_stream_chunk(chunk_data)
                        usage = _parse_usage(chunk.get("usage")) or usage
                        parsed_end_turn = _parse_end_turn(chunk.get("end_turn"))
                        if parsed_end_turn is not None:
                            end_turn = parsed_end_turn
                        for choice in _choices(chunk):
                            delta = choice.get("delta")
                            if not isinstance(delta, dict):
                                raise ClientError("模型返回了格式不合法的流式响应")
                            content = _text_content(delta.get("content"))
                            if content:
                                content_parts.append(content)
                                await on_text_delta(content)
                            reasoning = _text_content(delta.get("reasoning_content")) or _text_content(delta.get("reasoning"))
                            if reasoning:
                                reasoning_parts.append(reasoning)
                            _merge_tool_call_deltas(delta.get("tool_calls"), tool_call_parts)
        except httpx.RequestError as exc:
            raise ClientError(f"无法连接模型端点: {exc}") from exc

        return AssistantResponse(
            content="".join(content_parts) or None,
            tool_calls=_complete_tool_calls(tool_call_parts),
            usage=usage,
            end_turn=end_turn,
            reasoning="".join(reasoning_parts) or None,
        )

    @property
    def _endpoint(self) -> str:
        return f"{self._settings.base_url.rstrip('/')}/chat/completions"

    @property
    def _headers(self) -> dict[str, str]:
        if not self._settings.api_key:
            raise ClientError("缺少 OPENAI_API_KEY；请设置环境变量后重试。")
        return {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._settings.model,
            "messages": messages,
            "temperature": self._settings.temperature,
        }
        if tools:
            payload["tools"] = tools
        return payload

    async def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._settings.request_timeout_seconds) as client:
                response = await client.post(self._endpoint, headers=self._headers, json=payload)
                if response.is_error:
                    raise _http_error(response.status_code, response.text[:1000])
        except httpx.RequestError as exc:
            raise ClientError(f"无法连接模型端点: {exc}") from exc

        try:
            decoded = response.json()
        except json.JSONDecodeError as exc:
            raise ClientError("模型端点未返回 JSON 响应") from exc
        if not isinstance(decoded, dict):
            raise ClientError("模型端点未返回 JSON 对象")
        return decoded


async def _sse_chunks(response: httpx.Response) -> AsyncIterator[str]:
    """提取 SSE data 行；Chat Completions 每个事件都是单行 JSON。"""

    async for line in response.aiter_lines():
        if line.startswith("data:"):
            yield line[5:].lstrip()


def _parse_stream_chunk(data: str) -> dict[str, Any]:
    try:
        chunk = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ClientError("模型返回了格式不合法的流式响应") from exc
    if not isinstance(chunk, dict):
        raise ClientError("模型返回了格式不合法的流式响应")
    return chunk


def _choices(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    choices = chunk.get("choices", [])
    if not isinstance(choices, list) or not all(isinstance(choice, dict) for choice in choices):
        raise ClientError("模型返回了格式不合法的流式响应")
    return choices


def _merge_tool_call_deltas(raw_calls: Any, calls: dict[int, dict[str, Any]]) -> None:
    if raw_calls is None:
        return
    if not isinstance(raw_calls, list):
        raise ClientError("模型返回了格式不合法的流式工具调用")
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict) or type(index := raw_call.get("index")) is not int:
            raise ClientError("模型返回了格式不合法的流式工具调用")
        call = calls.setdefault(index, {"type": "function", "function": {"arguments": ""}})
        if "id" in raw_call:
            if not isinstance(raw_call["id"], str):
                raise ClientError("模型返回了格式不合法的流式工具调用")
            call["id"] = raw_call["id"]
        function = raw_call.get("function")
        if function is None:
            continue
        if not isinstance(function, dict):
            raise ClientError("模型返回了格式不合法的流式工具调用")
        if "name" in function:
            if not isinstance(function["name"], str):
                raise ClientError("模型返回了格式不合法的流式工具调用")
            call["function"]["name"] = function["name"]
        if "arguments" in function:
            if not isinstance(function["arguments"], str):
                raise ClientError("模型返回了格式不合法的流式工具调用")
            call["function"]["arguments"] += function["arguments"]


def _complete_tool_calls(parts: dict[int, dict[str, Any]]) -> tuple[ToolCall, ...]:
    return tuple(_parse_tool_call(parts[index]) for index in sorted(parts))


def _parse_response(response_data: dict[str, Any]) -> AssistantResponse:
    try:
        message = response_data["choices"][0]["message"]
    except (IndexError, KeyError, TypeError) as exc:
        raise ClientError("模型端点未返回预期的 Chat Completions 响应") from exc
    if not isinstance(message, dict):
        raise ClientError("模型端点未返回预期的 Chat Completions 响应")
    return AssistantResponse(
        content=_text_content(message.get("content")),
        tool_calls=tuple(_parse_tool_call(call) for call in message.get("tool_calls") or ()),
        usage=_parse_usage(response_data.get("usage")),
        end_turn=_parse_end_turn(response_data.get("end_turn")),
        reasoning=_text_content(message.get("reasoning_content")) or _text_content(message.get("reasoning")),
    )


def _text_content(content: Any) -> str | None:
    """兼容常见字符串与多段文本内容形式。"""

    if content is None or isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    raise ClientError("模型返回了无法处理的消息内容。")


def _parse_tool_call(raw_call: Any) -> ToolCall:
    """在不可信的模型边界立即校验工具参数 JSON。"""

    try:
        function = raw_call["function"]
        arguments = json.loads(function["arguments"])
        if not isinstance(arguments, dict):
            raise TypeError("arguments 不是对象")
        return ToolCall(id=raw_call["id"], name=function["name"], arguments=arguments)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ClientError("模型返回了格式不合法的工具调用。") from exc


def _parse_usage(raw_usage: Any) -> TokenUsage | None:
    """兼容未提供 usage 的端点；可用时保留服务端的精确计数。"""

    if not isinstance(raw_usage, dict):
        return None
    prompt_tokens = raw_usage.get("prompt_tokens")
    completion_tokens = raw_usage.get("completion_tokens")
    total_tokens = raw_usage.get("total_tokens")
    if not all(type(value) is int and value >= 0 for value in (prompt_tokens, completion_tokens, total_tokens)):
        return None
    if total_tokens < prompt_tokens + completion_tokens:
        return None
    return TokenUsage(prompt_tokens, completion_tokens, total_tokens)


def _parse_end_turn(raw_end_turn: Any) -> bool | None:
    """只接受端点显式给出的布尔值；其他格式回退到 tool-call 判断。"""

    return raw_end_turn if type(raw_end_turn) is bool else None


def _http_error(status_code: int, detail: str) -> ClientError:
    """保留兼容端点的原始详情，同时把可恢复的上下文超限变成明确类型。"""

    normalized = detail.lower()
    error_type = (
        ContextLimitError
        if "context_length_exceeded" in normalized
        or "maximum context length" in normalized
        else ClientError
    )
    return error_type(f"模型请求失败（HTTP {status_code}）：{detail}")
