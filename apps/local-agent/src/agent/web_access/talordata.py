"""TalorData SERP Search implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

import httpx

from agent.web_access.http import HttpApiClient
from agent.web_access.types import (
    ProviderResponseInvalid,
    ProviderSearchResult,
    WebAuthenticationFailed,
    WebQuotaExceeded,
    WebRateLimited,
    WebSearchTimeout,
    WebServiceUnavailable,
)

if TYPE_CHECKING:
    from agent.config.settings import Settings


class TalorDataProvider:
    """Search-only adapter for TalorData's Google SERP API."""

    name = "talordata"

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
            raise ValueError("TalorData API Key 不能为空")
        self._http = HttpApiClient(
            self.name,
            "https://serpapi.talordata.net",
            {"Authorization": f"Bearer {api_key.strip()}"},
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            retry_count=retry_count,
            # TalorData 的业务错误也返回 HTTP 200，由 search() 按 code 处理。
            quota_statuses=frozenset(),
            transport=transport,
        )

    @classmethod
    def from_settings(cls, api_key: str, settings: Settings) -> TalorDataProvider:
        return cls(
            api_key,
            connect_timeout_seconds=settings.web_connect_timeout_seconds,
            read_timeout_seconds=settings.web_read_timeout_seconds,
            retry_count=settings.web_retry_count,
        )

    async def search(
        self, query: str, *, max_results: int
    ) -> tuple[ProviderSearchResult, ...]:
        response = await self._http.post_form(
            "serp/v1/request",
            {
                "engine": "google",
                "q": query,
                "json": "1",
                "num": str(max_results),
                "filter": "1",
            },
            WebSearchTimeout,
        )
        code = response.get("code")
        if isinstance(code, bool) or not isinstance(code, int):
            raise ProviderResponseInvalid("搜索 Provider 返回格式异常。")
        if code != 0:
            _raise_business_error(code, response.get("data"))

        data = response.get("data")
        result = data.get("result") if isinstance(data, dict) else None
        raw_results = result.get("organic_results") if isinstance(result, dict) else None
        if not isinstance(raw_results, list):
            raise ProviderResponseInvalid("搜索 Provider 返回格式异常。")

        results: list[ProviderSearchResult] = []
        for raw in raw_results[:max_results]:
            if not isinstance(raw, dict):
                raise ProviderResponseInvalid("搜索 Provider 返回格式异常。")
            title = raw.get("title")
            url = raw.get("link")
            snippet = raw.get("description", raw.get("snippet", ""))
            if (
                not isinstance(title, str)
                or not title.strip()
                or not isinstance(url, str)
                or not isinstance(snippet, str)
            ):
                raise ProviderResponseInvalid("搜索 Provider 返回格式异常。")
            published_at = raw.get("date")
            if published_at is not None and not isinstance(published_at, str):
                published_at = None
            results.append(
                ProviderSearchResult(
                    title.strip(), url, snippet.strip(), None, published_at
                )
            )
        return tuple(results)


def _raise_business_error(code: int, detail: object) -> NoReturn:
    # 不把上游 detail 放进异常，避免响应中的敏感信息进入模型上下文或日志。
    if code == 401:
        raise WebAuthenticationFailed("Web Provider API Key 无效或无权访问。", status_code=401)
    if code == 429:
        raise WebRateLimited("Web Provider 请求频率受限。", status_code=429)
    if code == 504:
        raise WebSearchTimeout("搜索请求超时。", status_code=504)
    if code == 400 and isinstance(detail, str) and "package has expired" in detail.casefold():
        raise WebQuotaExceeded("Web Provider 配额已用尽。", status_code=400)
    if code >= 500:
        raise WebServiceUnavailable("Web Provider 暂时不可用。", status_code=code)
    raise ProviderResponseInvalid("Web Provider 拒绝了请求。", status_code=code)
