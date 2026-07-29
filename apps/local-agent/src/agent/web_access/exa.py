"""Exa Search and Contents implementation using the shared HTTP transport."""

from __future__ import annotations

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


class ExaProvider:
    """Provider adapter for Exa's Search and Contents APIs."""

    name = "exa"

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
            raise ValueError("Exa API Key 不能为空")
        self._http = HttpApiClient(
            self.name,
            "https://api.exa.ai",
            {"x-api-key": api_key.strip()},
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            retry_count=retry_count,
            quota_statuses=frozenset({402}),
            transport=transport,
        )

    @classmethod
    def from_settings(cls, api_key: str, settings: Settings) -> ExaProvider:
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
                "type": "auto",
                "numResults": max_results,
                # Highlights are the cheapest Exa response that still provides a
                # useful model-facing snippet without fetching whole pages.
                "contents": {
                    "highlights": {"query": query, "maxCharacters": 1_000}
                },
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
            highlights = raw.get("highlights", [])
            if (
                not isinstance(title, str)
                or not title.strip()
                or not isinstance(url, str)
                or not isinstance(highlights, list)
                or any(not isinstance(item, str) for item in highlights)
            ):
                raise ProviderResponseInvalid("搜索 Provider 返回格式异常。")
            published_at = raw.get("publishedDate")
            if published_at is not None and not isinstance(published_at, str):
                published_at = None
            results.append(
                ProviderSearchResult(
                    title.strip(),
                    url,
                    "\n\n".join(item.strip() for item in highlights if item.strip()),
                    None,
                    published_at,
                )
            )
        return tuple(results)

    async def fetch(
        self, urls: tuple[str, ...], *, query: str
    ) -> tuple[ProviderFetchResult, ...]:
        # Exa's full-text option does not accept a relevance query; asking for
        # highlights as well would add cost while the service needs page body text.
        data = await self._http.post_json(
            "contents",
            {
                "urls": list(urls),
                "text": {"maxCharacters": 12_000},
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
            content = raw.get("text")
            if not isinstance(url, str) or (
                content is not None and not isinstance(content, str)
            ):
                raise ProviderResponseInvalid("网页提取 Provider 返回格式异常。")
            results.append(ProviderFetchResult(url, content or ""))
        return tuple(results)
