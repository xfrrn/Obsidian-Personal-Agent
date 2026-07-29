"""Session-scoped web services and their bounded search-result cache."""

from __future__ import annotations

import hashlib
import asyncio
import logging
import math
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable

from agent.web_access.local_fetch import (
    LocalFetchError,
    LocalFetchResult,
    LocalStaticFetcher,
    safe_web_url as _safe_local_url,
)

from agent.web_access.types import (
    FetchFailure,
    FetchProvider,
    FetchedSource,
    ProviderFetchResult,
    ProviderResponseInvalid,
    ProviderSearchResult,
    SearchProvider,
    SearchRecord,
    SearchRecordExpired,
    SearchSource,
    SourceIdInvalid,
    WebAccessError,
    WebContentEmpty,
    WebFetchFailed,
    WebFetchResult,
    WebLimitExceeded,
    WebRequestInvalid,
    WebServiceUnavailable,
)


MAX_SEARCHES_PER_TASK = 3
MAX_SEARCH_RESULTS = 8
MAX_FETCH_SOURCES = 4
MIN_CHARS_PER_SOURCE = 1_000
MAX_CHARS_PER_SOURCE = 12_000
MAX_QUERY_CHARS = 500
_MAX_TITLE_CHARS = 500
_MAX_SNIPPET_CHARS = 2_000
_LOGGER = logging.getLogger(__name__)


class SessionSearchCache:
    """A lazy-pruned FIFO cache; no background task or persistence is needed."""

    def __init__(
        self,
        ttl_seconds: float,
        max_records: int,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or max_records <= 0:
            raise ValueError("搜索缓存 TTL 和容量必须大于 0")
        self._ttl_seconds = ttl_seconds
        self._max_records = max_records
        self._now = now
        self._records: OrderedDict[str, SearchRecord] = OrderedDict()
        self._queries: dict[str, str] = {}

    def find_query(self, normalized_query: str) -> SearchRecord | None:
        self._prune()
        search_id = self._queries.get(normalized_query)
        return self._records.get(search_id) if search_id is not None else None

    def get(self, search_id: str) -> SearchRecord:
        self._prune()
        record = self._records.get(search_id)
        if record is None:
            # Missing and expired IDs intentionally share one response, avoiding an
            # enumeration oracle while telling the model how to recover.
            raise SearchRecordExpired("搜索记录不存在或已过期，请重新调用 web_search。")
        return record

    def put(self, normalized_query: str, record: SearchRecord) -> None:
        old_search_id = self._queries.get(normalized_query)
        if old_search_id is not None:
            self._remove(old_search_id)
        self._records[record.search_id] = record
        self._queries[normalized_query] = record.search_id
        while len(self._records) > self._max_records:
            search_id, _ = self._records.popitem(last=False)
            self._queries = {
                query: value
                for query, value in self._queries.items()
                if value != search_id
            }

    def _prune(self) -> None:
        cutoff = self._now() - self._ttl_seconds
        for search_id, record in tuple(self._records.items()):
            if record.created_at > cutoff:
                break
            self._remove(search_id)

    def _remove(self, search_id: str) -> None:
        self._records.pop(search_id, None)
        self._queries = {
            query: value
            for query, value in self._queries.items()
            if value != search_id
        }


class WebSearchService:
    def __init__(
        self,
        provider: SearchProvider,
        cache: SessionSearchCache,
        *,
        now: Callable[[], float] = time.monotonic,
        new_search_id: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self._provider = provider
        self._cache = cache
        self._now = now
        self._new_search_id = new_search_id
        # A Session runs one active turn at a time, so one fixed-size budget is
        # enough; a per-submission dictionary would grow for the Session lifetime.
        self._submission_id: int | None = None
        self._search_count = 0
        self._queries: set[str] = set()

    async def search(self, query: str, submission_id: int) -> SearchRecord:
        clean_query, normalized_query = _queries(query)
        cached = self._cache.find_query(normalized_query)
        if cached is not None:
            _LOGGER.info(
                "web.search.cache_hit",
                extra={
                    "provider": self._provider.name,
                    "query_hash": _query_hash(normalized_query),
                    "query_chars": len(clean_query),
                    "result_count": len(cached.sources),
                    "cache_hit": True,
                },
            )
            return cached

        self._reserve_search(submission_id, normalized_query)
        started_at = time.monotonic()
        try:
            raw_results = await self._provider.search(
                clean_query, max_results=MAX_SEARCH_RESULTS
            )
            sources: list[SearchSource] = []
            seen_urls: set[str] = set()
            if not isinstance(raw_results, tuple):
                raise ProviderResponseInvalid("搜索 Provider 返回格式异常。")
            for result in raw_results[:MAX_SEARCH_RESULTS]:
                if (
                    not isinstance(result, ProviderSearchResult)
                    or not isinstance(result.title, str)
                    or not result.title.strip()
                    or not isinstance(result.url, str)
                    or not isinstance(result.snippet, str)
                    or (
                        result.score is not None
                        and (
                            isinstance(result.score, bool)
                            or not isinstance(result.score, (int, float))
                            or not math.isfinite(result.score)
                        )
                    )
                    or (
                        result.published_at is not None
                        and not isinstance(result.published_at, str)
                    )
                ):
                    raise ProviderResponseInvalid("搜索 Provider 返回格式异常。")
                try:
                    url, domain = safe_web_url(result.url)
                except WebRequestInvalid:
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                sources.append(
                    SearchSource(
                        source_id=f"source_{len(sources) + 1}",
                        title=result.title.strip()[:_MAX_TITLE_CHARS],
                        url=url,
                        snippet=result.snippet.strip()[:_MAX_SNIPPET_CHARS],
                        score=result.score,
                        published_at=result.published_at,
                        domain=domain,
                    )
                )
            record = SearchRecord(
                self._new_search_id(), clean_query, tuple(sources), self._now()
            )
            self._cache.put(normalized_query, record)
            _LOGGER.info(
                "web.search.completed",
                extra={
                    "provider": self._provider.name,
                    "query_hash": _query_hash(normalized_query),
                    "query_chars": len(clean_query),
                    "result_count": len(sources),
                    "duration_ms": _elapsed_ms(started_at),
                    "cache_hit": False,
                },
            )
            return record
        except WebAccessError as exc:
            _log_failure("web.search.failed", self._provider.name, started_at, exc)
            raise
        except Exception as exc:
            _log_failure("web.search.failed", self._provider.name, started_at, exc)
            raise WebServiceUnavailable("搜索服务暂时不可用。") from exc

    def _reserve_search(self, submission_id: int, normalized_query: str) -> None:
        if submission_id <= 0:
            raise WebRequestInvalid("web_search 缺少有效的所属任务。")
        if submission_id != self._submission_id:
            self._submission_id = submission_id
            self._search_count = 0
            self._queries.clear()
        if normalized_query in self._queries:
            raise WebLimitExceeded("同一任务不能重复提交相同的搜索词。")
        if self._search_count >= MAX_SEARCHES_PER_TASK:
            raise WebLimitExceeded(
                f"单个任务最多调用 web_search {MAX_SEARCHES_PER_TASK} 次。"
            )
        self._queries.add(normalized_query)
        self._search_count += 1


class WebFetchService:
    def __init__(
        self,
        provider: FetchProvider,
        cache: SessionSearchCache,
        local_fetcher: LocalStaticFetcher | None = None,
    ) -> None:
        self._provider = provider
        self._cache = cache
        self._local_fetcher = local_fetcher or LocalStaticFetcher()

    async def fetch(
        self,
        search_id: str,
        source_ids: tuple[str, ...],
        query: str,
        max_chars_per_source: int,
    ) -> WebFetchResult:
        if not search_id or len(search_id) > 64:
            raise WebRequestInvalid("search_id 无效。")
        if not 1 <= len(source_ids) <= MAX_FETCH_SOURCES:
            raise WebLimitExceeded(
                f"web_fetch 每次只能读取 1 到 {MAX_FETCH_SOURCES} 个来源。"
            )
        if len(set(source_ids)) != len(source_ids):
            raise WebRequestInvalid("source_ids 不能重复。")
        if not MIN_CHARS_PER_SOURCE <= max_chars_per_source <= MAX_CHARS_PER_SOURCE:
            raise WebLimitExceeded(
                f"max_chars_per_source 必须在 {MIN_CHARS_PER_SOURCE} 到 "
                f"{MAX_CHARS_PER_SOURCE} 之间。"
            )
        clean_query, _ = _queries(query)
        record = self._cache.get(search_id)
        by_id = {source.source_id: source for source in record.sources}
        missing = [source_id for source_id in source_ids if source_id not in by_id]
        if missing:
            raise SourceIdInvalid(f"来源编号无效: {', '.join(missing)}")
        selected = tuple(by_id[source_id] for source_id in source_ids)

        started_at = time.monotonic()
        local_results = await asyncio.gather(
            *(
                self._local_fetcher.fetch(source.url, max_chars_per_source)
                for source in selected
            ),
            return_exceptions=True,
        )
        content_by_url: dict[str, str] = {}
        method_by_url: dict[str, str] = {}
        fallback_urls: list[str] = []
        for source, result in zip(selected, local_results, strict=True):
            if isinstance(result, LocalFetchResult):
                content_by_url[source.url] = result.content
                method_by_url[source.url] = "local"
            else:
                fallback_urls.append(source.url)

        provider_error: WebAccessError | None = None
        try:
            provider_results = (
                await self._provider.fetch(tuple(fallback_urls), query=clean_query)
                if fallback_urls
                else ()
            )
            if not isinstance(provider_results, tuple) or any(
                not isinstance(result, ProviderFetchResult)
                or not isinstance(result.url, str)
                or not isinstance(result.content, str)
                or (
                    result.provider is not None
                    and not isinstance(result.provider, str)
                )
                for result in provider_results
            ):
                raise ProviderResponseInvalid("网页提取 Provider 返回格式异常。")

            for result in provider_results:
                try:
                    url, _ = safe_web_url(result.url)
                except WebRequestInvalid:
                    continue
                if url in fallback_urls:
                    content_by_url.setdefault(url, result.content)
                    method_by_url.setdefault(
                        url, result.provider or self._provider.name
                    )
        except WebAccessError as exc:
            _log_fetch_failure(selected, self._provider.name, started_at, exc)
            provider_error = exc
        except Exception as exc:
            _log_fetch_failure(selected, self._provider.name, started_at, exc)
            provider_error = WebFetchFailed("网页提取服务暂时不可用。")

        fetched: list[FetchedSource] = []
        failures: list[FetchFailure] = []
        empty_count = 0
        for source in selected:
            content = content_by_url.get(source.url)
            if content is None:
                failures.append(
                    FetchFailure(
                        source.source_id,
                        provider_error.code if provider_error else WebFetchFailed.code,
                        "正文回退提取失败。"
                        if provider_error
                        else "Provider 未返回该来源的正文。",
                    )
                )
                continue
            content = content.strip()
            if not content:
                empty_count += 1
                failures.append(
                    FetchFailure(
                        source.source_id,
                        WebContentEmpty.code,
                        "该来源没有可用正文。",
                    )
                )
                continue
            fetched.append(
                FetchedSource(
                    source_id=source.source_id,
                    title=source.title,
                    url=source.url,
                    domain=source.domain,
                    published_at=source.published_at,
                    content=content[:max_chars_per_source],
                    truncated=len(content) > max_chars_per_source,
                    fetch_method=method_by_url[source.url],
                    fallback_used=source.url in fallback_urls,
                )
            )

        if not fetched:
            error: WebAccessError = (
                WebContentEmpty("所有来源的正文均为空。")
                if empty_count == len(selected)
                else WebFetchFailed("所有来源均提取失败。")
            )
            _log_fetch_failure(selected, self._provider.name, started_at, error)
            raise provider_error or error
        _LOGGER.info(
            "web.fetch.completed",
            extra={
                "domains": [source.domain for source in fetched],
                "source_count": len(fetched),
                "duration_ms": _elapsed_ms(started_at),
                "content_chars": sum(len(source.content) for source in fetched),
                "fetch_method": (
                    fetched[0].fetch_method
                    if all(
                        source.fetch_method == fetched[0].fetch_method
                        for source in fetched
                    )
                    else "mixed"
                ),
                "fallback_used": bool(fallback_urls),
            },
        )
        return WebFetchResult(search_id, clean_query, tuple(fetched), tuple(failures))


def safe_web_url(value: str) -> tuple[str, str]:
    """Validate cached provider URLs before any fetch implementation receives them."""
    try:
        return _safe_local_url(value)
    except LocalFetchError as exc:
        raise WebRequestInvalid("Provider 返回了不安全或无效的 URL。") from exc


def _queries(query: str) -> tuple[str, str]:
    if not isinstance(query, str):
        raise WebRequestInvalid("query 必须是字符串。")
    clean = " ".join(query.split())
    if not clean or len(clean) > MAX_QUERY_CHARS:
        raise WebRequestInvalid(f"query 必须为 1 到 {MAX_QUERY_CHARS} 个字符。")
    return clean, clean.casefold()


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]


def _elapsed_ms(started_at: float) -> int:
    return round((time.monotonic() - started_at) * 1000)


def _log_failure(
    event: str, provider: str, started_at: float, error: BaseException
) -> None:
    _LOGGER.warning(
        event,
        extra={
            "provider": provider,
            "duration_ms": _elapsed_ms(started_at),
            "error_type": type(error).__name__,
            "http_status": getattr(error, "status_code", None),
        },
    )


def _log_fetch_failure(
    sources: tuple[SearchSource, ...],
    method: str,
    started_at: float,
    error: BaseException,
) -> None:
    _LOGGER.warning(
        "web.fetch.failed",
        extra={
            "domains": [source.domain for source in sources],
            "http_status": getattr(error, "status_code", None),
            "duration_ms": _elapsed_ms(started_at),
            "content_chars": 0,
            "fetch_method": method,
            "fallback_used": True,
            "failure_reason": type(error).__name__,
        },
    )
