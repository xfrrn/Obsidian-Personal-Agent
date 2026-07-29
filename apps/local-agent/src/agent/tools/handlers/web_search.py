"""Stable model-facing web search tool."""

from __future__ import annotations

import json

from agent.permissions import ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.invocation import ToolCallContext
from agent.tools.types import ToolExecution, ToolSpec
from agent.web_access.services import MAX_QUERY_CHARS, WebSearchService
from agent.web_access.types import WebAccessError


class WebSearchTool:
    uses_call_context = True
    supports_parallel_tool_calls = False
    required_access = ToolAccess.READ_ONLY
    spec = ToolSpec(
        name="web_search",
        description=(
            "搜索互联网并返回最多 8 个候选来源。结果只有摘要；需要正文时使用 "
            "web_fetch。不要重复提交相同查询。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_QUERY_CHARS,
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )

    def __init__(self, service: WebSearchService) -> None:
        self._service = service

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
        call_context: ToolCallContext | None = None,
    ) -> str | ToolExecution:
        if set(arguments) != {"query"} or not isinstance(arguments["query"], str):
            raise ValueError("参数必须且只能包含字符串 query")
        if call_context is None or call_context.submission_id is None:
            raise RuntimeError("web_search 缺少所属任务")
        try:
            record = await self._service.search(
                arguments["query"], call_context.submission_id
            )
        except WebAccessError as exc:
            return _error_execution(exc)
        return json.dumps(
            {
                "ok": True,
                "search_id": record.search_id,
                "query": record.query,
                "trust": "untrusted_web_search_results",
                "sources": [
                    {
                        "source_id": source.source_id,
                        "title": source.title,
                        "url": source.url,
                        "snippet": source.snippet,
                        "score": source.score,
                        "published_at": source.published_at,
                        "domain": source.domain,
                    }
                    for source in record.sources
                ],
            },
            ensure_ascii=False,
        )


def _error_execution(error: WebAccessError) -> ToolExecution:
    return ToolExecution(
        json.dumps({"ok": False, "error": error.as_dict()}, ensure_ascii=False),
        is_error=True,
    )
