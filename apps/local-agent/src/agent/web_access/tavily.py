"""Tavily Search and Extract implementation using the existing HTTP client."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import httpx

from agent.web_access.http import HttpApiClient
from agent.web_access.types import (
    ProviderFetchResult,
    ProviderResponseInvalid,
    ProviderSearchResult,
    WebFetchTimeout,
    WebSearchTimeout,
)

if TYPE_CHECKING:
    from agent.config.settings import Settings


class TavilyProvider:
    """Stateless provider; one instance may be shared by multiple Agent Sessions."""

    name = "tavily"

    def __init__(
        self,
        api_key: str,
        *,
        connect_timeout_seconds: float = 5,
        read_timeout_seconds: float = 20,
        retry_count: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Tavily API Key 不能为空")
        self._read_timeout = read_timeout_seconds
        self._http = HttpApiClient(
            self.name,
            "https://api.tavily.com",
            {"Authorization": f"Bearer {api_key.strip()}"},
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            retry_count=retry_count,
            quota_statuses=frozenset({432, 433}),
            transport=transport,
        )

    @classmethod
    def from_settings(cls, api_key: str, settings: Settings) -> TavilyProvider:
        return cls(
            api_key,
            connect_timeout_seconds=settings.web_connect_timeout_seconds,
            read_timeout_seconds=settings.web_read_timeout_seconds,
            retry_count=settings.web_retry_count,
        )

    async def search(
        self, query: str, *, max_results: int
    ) -> tuple[ProviderSearchResult, ...]:
        data = await self._http.post_json(
            "search",
            {
                "query": query,
                "search_depth": "basic",
                "max_results": max_results,
                "topic": "general",
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
                "auto_parameters": False,
                "include_usage": False,
            },
            WebSearchTimeout,
        )
        raw_results = data.get("results")
        if not isinstance(raw_results, list):
            raise ProviderResponseInvalid("搜索 Provider 返回格式异常。")
        results: list[ProviderSearchResult] = []
        for raw in raw_results[:max_results]:
            if not isinstance(raw, dict):
                raise ProviderResponseInvalid("搜索 Provider 返回格式异常。")
            title = raw.get("title")
            url = raw.get("url")
            snippet = raw.get("content")
            score = raw.get("score")
            if (
                not isinstance(title, str)
                or not title.strip()
                or not isinstance(url, str)
                or not isinstance(snippet, str)
                or (
                    score is not None
                    and (
                        isinstance(score, bool)
                        or not isinstance(score, (int, float))
                        or not math.isfinite(score)
                    )
                )
            ):
                raise ProviderResponseInvalid("搜索 Provider 返回格式异常。")
            published_at = raw.get("published_at", raw.get("published_date"))
            if published_at is not None and not isinstance(published_at, str):
                published_at = None
            results.append(
                ProviderSearchResult(
                    title.strip(),
                    url,
                    snippet,
                    float(score) if score is not None else None,
                    published_at,
                )
            )
        return tuple(results)

    async def fetch(
        self, urls: tuple[str, ...], *, query: str
    ) -> tuple[ProviderFetchResult, ...]:
        data = await self._http.post_json(
            "extract",
            {
                "urls": list(urls),
                "query": query,
                "chunks_per_source": 5,
                "extract_depth": "basic",
                "include_images": False,
                "include_favicon": False,
                "format": "markdown",
                "timeout": min(self._read_timeout, 60),
                "include_usage": False,
            },
            WebFetchTimeout,
        )
        raw_results = data.get("results")
        if not isinstance(raw_results, list):
            raise ProviderResponseInvalid("网页提取 Provider 返回格式异常。")
        results: list[ProviderFetchResult] = []
        for raw in raw_results:
            if not isinstance(raw, dict):
                raise ProviderResponseInvalid("网页提取 Provider 返回格式异常。")
            url = raw.get("url")
            content = raw.get("raw_content")
            if not isinstance(url, str) or (
                content is not None and not isinstance(content, str)
            ):
                raise ProviderResponseInvalid("网页提取 Provider 返回格式异常。")
            # Tavily may represent a successfully resolved but empty page as null.
            # Preserve that distinction so the service can return CONTENT_EMPTY.
            results.append(ProviderFetchResult(url, content or ""))
        return tuple(results)
