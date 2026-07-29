"""Concurrency-safe API-key rotation at the provider boundary."""

from __future__ import annotations

import asyncio
import logging
from typing import Generic, TypeVar

from agent.web_access.types import (
    FetchProvider,
    ProviderFetchResult,
    ProviderSearchResult,
    SearchProvider,
    WebAccessError,
    WebAuthenticationFailed,
    WebQuotaExceeded,
    WebRateLimited,
)


_LOGGER = logging.getLogger(__name__)
_ProviderT = TypeVar("_ProviderT")
_KEY_FAILURES = (WebAuthenticationFailed, WebRateLimited, WebQuotaExceeded)


class _RoundRobin(Generic[_ProviderT]):
    def __init__(self, providers: tuple[_ProviderT, ...]) -> None:
        if len(providers) < 2:
            raise ValueError("轮换池至少需要两个 Provider 实例")
        self.providers = providers
        self._index = 0
        self._lock = asyncio.Lock()

    async def candidates(self) -> tuple[tuple[int, _ProviderT], ...]:
        # 只在更新游标时持锁；网络请求并行执行，不会被一个慢 Key 串行阻塞。
        async with self._lock:
            start = self._index
            self._index = (self._index + 1) % len(self.providers)
        ordered: list[tuple[int, _ProviderT]] = []
        for offset in range(len(self.providers)):
            index = (start + offset) % len(self.providers)
            ordered.append((index, self.providers[index]))
        return tuple(ordered)


class RotatingSearchProvider:
    """Round-robin search, failing over only for key-scoped failures."""

    def __init__(self, providers: tuple[SearchProvider, ...]) -> None:
        self.name = _provider_name(providers)
        self._pool = _RoundRobin(providers)

    async def search(
        self, query: str, *, max_results: int
    ) -> tuple[ProviderSearchResult, ...]:
        last_error: WebAccessError | None = None
        for key_index, provider in await self._pool.candidates():
            try:
                return await provider.search(query, max_results=max_results)
            except _KEY_FAILURES as exc:
                last_error = exc
                _log_failover(self.name, key_index, len(self._pool.providers), exc)
        assert last_error is not None
        raise last_error


class RotatingFetchProvider:
    """Round-robin fetch, failing over only for key-scoped failures."""

    def __init__(self, providers: tuple[FetchProvider, ...]) -> None:
        self.name = _provider_name(providers)
        self._pool = _RoundRobin(providers)

    async def fetch(
        self, urls: tuple[str, ...], *, query: str
    ) -> tuple[ProviderFetchResult, ...]:
        last_error: WebAccessError | None = None
        for key_index, provider in await self._pool.candidates():
            try:
                return await provider.fetch(urls, query=query)
            except _KEY_FAILURES as exc:
                last_error = exc
                _log_failover(self.name, key_index, len(self._pool.providers), exc)
        assert last_error is not None
        raise last_error


def _provider_name(providers: tuple[SearchProvider, ...] | tuple[FetchProvider, ...]) -> str:
    if len(providers) < 2 or any(
        provider.name != providers[0].name for provider in providers
    ):
        raise ValueError("轮换池必须包含至少两个同供应商 Provider")
    return providers[0].name


def _log_failover(
    provider: str, key_index: int, key_count: int, error: WebAccessError
) -> None:
    _LOGGER.warning(
        "web.provider.key_failover",
        extra={
            "provider": provider,
            "key_index": key_index + 1,
            "key_count": key_count,
            "error_type": type(error).__name__,
            "http_status": error.status_code,
        },
    )
