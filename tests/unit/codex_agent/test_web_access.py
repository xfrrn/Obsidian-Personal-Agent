"""Provider-neutral web access, limits, and Agent-loop integration checks."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from unittest.mock import patch

import httpx

from agent.config.settings import Settings
from agent.core.handle import AgentHandle
from agent.core.loop import create_session, start_agent
from agent.core.turn.public_events import PublicEventAdapter
from agent.llm.types import AssistantResponse, ToolCall
from agent.protocol.event import EventKind
from agent.protocol.op import UserInput
from agent.web_access.config import WebProviderConfig, _api_keys, configured_web_providers
from agent.web_access.exa import ExaProvider
from agent.web_access.key_pool import RotatingFetchProvider, RotatingSearchProvider
from agent.web_access.local_fetch import (
    LocalFetchError,
    LocalFetchResult,
    LocalStaticFetcher,
    _PublicNetworkBackend,
)
from agent.web_access.services import (
    SessionSearchCache,
    WebFetchService,
    WebSearchService,
    safe_web_url,
)
from agent.web_access.tavily import TavilyProvider
from agent.web_access.talordata import TalorDataProvider
from agent.web_access.types import (
    ProviderFetchResult,
    ProviderResponseInvalid,
    ProviderSearchResult,
    SearchRecordExpired,
    SourceIdInvalid,
    WebAuthenticationFailed,
    WebLimitExceeded,
    WebQuotaExceeded,
    WebRateLimited,
    WebRequestInvalid,
)


class FakeWebProvider:
    name = "fake"

    def __init__(self) -> None:
        self.search_calls: list[str] = []
        self.fetch_calls: list[tuple[tuple[str, ...], str]] = []

    async def search(
        self, query: str, *, max_results: int
    ) -> tuple[ProviderSearchResult, ...]:
        self.search_calls.append(query)
        return (
            ProviderSearchResult(
                f"Title {query}",
                f"https://example.com/{len(self.search_calls)}",
                f"Snippet {query}",
                0.9,
            ),
            # Unsafe provider results are removed before the model sees them.
            ProviderSearchResult("Local", "http://127.0.0.1/admin", "secret", 1.0),
        )[:max_results]

    async def fetch(
        self, urls: tuple[str, ...], *, query: str
    ) -> tuple[ProviderFetchResult, ...]:
        self.fetch_calls.append((urls, query))
        return (ProviderFetchResult(urls[0], "useful content" * 200),)


class FailingLocalFetcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch(self, url: str, max_chars: int) -> LocalFetchResult:
        self.calls.append(url)
        raise LocalFetchError("request_failed")


class SuccessfulLocalFetcher:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[str] = []

    async def fetch(self, url: str, max_chars: int) -> LocalFetchResult:
        self.calls.append(url)
        return LocalFetchResult(self.content[: max_chars + 1], 200)


class KeyedFakeProvider:
    name = "fake"

    def __init__(
        self,
        label: str,
        *,
        search_error: WebAuthenticationFailed
        | ProviderResponseInvalid
        | None = None,
        fetch_error: WebRateLimited | None = None,
    ) -> None:
        self.label = label
        self.search_error = search_error
        self.fetch_error = fetch_error
        self.search_calls = 0
        self.fetch_calls = 0

    async def search(
        self, query: str, *, max_results: int
    ) -> tuple[ProviderSearchResult, ...]:
        self.search_calls += 1
        await asyncio.sleep(0)
        if self.search_error is not None:
            raise self.search_error
        return (ProviderSearchResult(self.label, f"https://{self.label}.example", query),)

    async def fetch(
        self, urls: tuple[str, ...], *, query: str
    ) -> tuple[ProviderFetchResult, ...]:
        self.fetch_calls += 1
        await asyncio.sleep(0)
        if self.fetch_error is not None:
            raise self.fetch_error
        return (ProviderFetchResult(urls[0], f"{self.label}:{query}"),)


class RotatingProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_searches_round_robin_without_serializing_requests(self) -> None:
        first = KeyedFakeProvider("first")
        second = KeyedFakeProvider("second")
        provider = RotatingSearchProvider((first, second))

        await asyncio.gather(
            *(provider.search(f"query-{index}", max_results=1) for index in range(6))
        )

        self.assertEqual((first.search_calls, second.search_calls), (3, 3))

    async def test_key_failures_switch_provider_but_format_errors_do_not(self) -> None:
        failed = KeyedFakeProvider(
            "failed",
            search_error=WebAuthenticationFailed("invalid", status_code=401),
        )
        healthy = KeyedFakeProvider("healthy")
        results = await RotatingSearchProvider((failed, healthy)).search(
            "query", max_results=1
        )

        self.assertEqual(results[0].title, "healthy")
        self.assertEqual((failed.search_calls, healthy.search_calls), (1, 1))

        malformed = KeyedFakeProvider(
            "malformed", search_error=ProviderResponseInvalid("bad response")
        )
        unused = KeyedFakeProvider("unused")
        with self.assertRaises(ProviderResponseInvalid):
            await RotatingSearchProvider((malformed, unused)).search(
                "query", max_results=1
            )
        self.assertEqual(unused.search_calls, 0)

    async def test_rate_limited_fetch_switches_to_next_key(self) -> None:
        limited = KeyedFakeProvider(
            "limited", fetch_error=WebRateLimited("limited", status_code=429)
        )
        healthy = KeyedFakeProvider("healthy")

        results = await RotatingFetchProvider((limited, healthy)).fetch(
            ("https://example.com",), query="intent"
        )

        self.assertEqual(results[0].content, "healthy:intent")
        self.assertEqual(results[0].provider, "fake")
        self.assertEqual((limited.fetch_calls, healthy.fetch_calls), (1, 1))

    async def test_mixed_fetch_pool_reports_the_provider_that_won(self) -> None:
        tavily = KeyedFakeProvider("tavily")
        tavily.name = "tavily"
        exa = KeyedFakeProvider("exa")
        exa.name = "exa"
        provider = RotatingFetchProvider((tavily, exa))

        first = await provider.fetch(("https://example.com",), query="one")
        second = await provider.fetch(("https://example.com",), query="two")

        self.assertEqual(provider.name, "auto")
        self.assertEqual((first[0].provider, second[0].provider), ("tavily", "exa"))


class WebServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_search_reuses_queries_and_limits_provider_calls_per_task(self) -> None:
        provider = FakeWebProvider()
        cache = SessionSearchCache(1_800, 20)
        search_ids = iter((f"search-{index}" for index in range(10)))
        service = WebSearchService(
            provider, cache, new_search_id=lambda: next(search_ids)
        )

        first = await service.search("  Python   asyncio  ", 1)
        cached = await service.search("python asyncio", 1)
        await service.search("second", 1)
        await service.search("third", 1)
        with self.assertRaises(WebLimitExceeded):
            await service.search("fourth", 1)
        await service.search("fourth", 2)

        self.assertEqual(first.search_id, cached.search_id)
        self.assertEqual(len(first.sources), 1)
        self.assertEqual(
            provider.search_calls,
            ["Python asyncio", "second", "third", "fourth"],
        )

    async def test_cache_expires_and_evicts_oldest_records(self) -> None:
        clock = [10.0]
        provider = FakeWebProvider()
        cache = SessionSearchCache(30, 2, now=lambda: clock[0])
        search_ids = iter(("one", "two", "three"))
        service = WebSearchService(
            provider,
            cache,
            now=lambda: clock[0],
            new_search_id=lambda: next(search_ids),
        )

        first = await service.search("one", 1)
        await service.search("two", 1)
        await service.search("three", 1)
        with self.assertRaises(SearchRecordExpired):
            cache.get(first.search_id)
        clock[0] += 31
        with self.assertRaises(SearchRecordExpired):
            cache.get("three")

    async def test_fetch_resolves_cached_urls_and_returns_partial_failures(self) -> None:
        provider = FakeWebProvider()
        local_fetcher = FailingLocalFetcher()
        cache = SessionSearchCache(1_800, 20)
        search = WebSearchService(
            provider, cache, new_search_id=lambda: "search-id"
        )
        record = await search.search("query", 1)
        result = await WebFetchService(provider, cache, local_fetcher).fetch(
            record.search_id, ("source_1",), "reading intent", 1_000
        )

        self.assertEqual(
            provider.fetch_calls,
            [(("https://example.com/1",), "reading intent")],
        )
        self.assertEqual(result.sources[0].source_id, "source_1")
        self.assertTrue(result.sources[0].truncated)
        self.assertEqual(len(result.sources[0].content), 1_000)
        self.assertEqual(result.sources[0].fetch_method, "fake")
        self.assertTrue(result.sources[0].fallback_used)
        with self.assertRaises(SourceIdInvalid):
            await WebFetchService(provider, cache, local_fetcher).fetch(
                record.search_id, ("source_99",), "query", 1_000
            )

    async def test_fetch_uses_local_content_without_calling_provider(self) -> None:
        provider = FakeWebProvider()
        local_fetcher = SuccessfulLocalFetcher("local body " * 200)
        cache = SessionSearchCache(1_800, 20)
        record = await WebSearchService(
            provider, cache, new_search_id=lambda: "search-id"
        ).search("query", 1)

        result = await WebFetchService(provider, cache, local_fetcher).fetch(
            record.search_id, ("source_1",), "intent", 1_000
        )

        self.assertEqual(provider.fetch_calls, [])
        self.assertEqual(local_fetcher.calls, ["https://example.com/1"])
        self.assertEqual(result.sources[0].fetch_method, "local")
        self.assertFalse(result.sources[0].fallback_used)

    async def test_only_public_http_urls_are_accepted(self) -> None:
        self.assertEqual(
            safe_web_url("HTTPS://Example.COM/path#fragment"),
            ("https://example.com/path", "example.com"),
        )
        for url in (
            "file:///etc/passwd",
            "http://localhost/",
            "http://0.0.0.0/",
            "http://127.0.0.1/",
            "http://[::1]/",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data",
            "https://user:password@example.com/",
            "https://example.com:8443/",
        ):
            with self.subTest(url=url), self.assertRaises(WebRequestInvalid):
                safe_web_url(url)

    async def test_malformed_provider_fetch_result_is_typed(self) -> None:
        provider = FakeWebProvider()
        cache = SessionSearchCache(1_800, 20)
        record = await WebSearchService(
            provider, cache, new_search_id=lambda: "search-id"
        ).search("query", 1)

        async def malformed_fetch(
            urls: tuple[str, ...], *, query: str
        ) -> tuple[object, ...]:
            return (object(),)

        provider.fetch = malformed_fetch  # type: ignore[method-assign]
        with self.assertRaises(ProviderResponseInvalid):
            await WebFetchService(provider, cache, FailingLocalFetcher()).fetch(
                record.search_id, ("source_1",), "query", 1_000
            )


class LocalStaticFetcherTest(unittest.IsolatedAsyncioTestCase):
    async def test_follows_checked_redirect_and_extracts_markdown(self) -> None:
        requests: list[str] = []
        html = (
            "<html><body><article><h1>Static page</h1><p>"
            + "Useful article text. " * 40
            + "</p></article></body></html>"
        )

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if request.url.host == "example.com":
                return httpx.Response(
                    302, headers={"Location": "https://example.org/article"}
                )
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                content=html,
            )

        result = await LocalStaticFetcher(
            transport=httpx.MockTransport(respond)
        ).fetch("https://example.com/start", 1_000)

        self.assertEqual(
            requests,
            ["https://example.com/start", "https://example.org/article"],
        )
        self.assertEqual(result.status_code, 200)
        self.assertIn("Static page", result.content)
        self.assertIn("Useful article text", result.content)

    async def test_rejects_unsafe_redirect_before_second_request(self) -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                302,
                headers={
                    "Location": "http://169.254.169.254/latest/meta-data"
                },
            )

        with self.assertRaises(LocalFetchError) as raised:
            await LocalStaticFetcher(
                transport=httpx.MockTransport(respond)
            ).fetch("https://example.com/start", 1_000)

        self.assertEqual(raised.exception.reason, "private_network")
        self.assertEqual(len(requests), 1)

    async def test_rejects_non_html_and_oversized_responses(self) -> None:
        for response, reason in (
            (
                httpx.Response(
                    200,
                    headers={"Content-Type": "application/json"},
                    json={"value": "not html"},
                ),
                "unsupported_content_type",
            ),
            (
                httpx.Response(
                    200,
                    headers={
                        "Content-Type": "text/html",
                        "Content-Length": "101",
                    },
                    content=b"x",
                ),
                "response_too_large",
            ),
        ):
            with self.subTest(reason=reason), self.assertRaises(
                LocalFetchError
            ) as raised:
                await LocalStaticFetcher(
                    max_response_bytes=100,
                    transport=httpx.MockTransport(lambda request, r=response: r),
                ).fetch("https://example.com/page", 1_000)
            self.assertEqual(raised.exception.reason, reason)

    async def test_dns_backend_rejects_private_answers_and_pins_public_ip(self) -> None:
        async def private_resolver(host: str, port: int) -> tuple[str, ...]:
            return ("93.184.216.34", "127.0.0.1")

        with self.assertRaises(LocalFetchError):
            await _PublicNetworkBackend(resolver=private_resolver).connect_tcp(
                "example.com", 443
            )

        calls: list[str] = []

        class RecordingBackend:
            async def connect_tcp(self, host: str, port: int, **kwargs: object) -> object:
                calls.append(host)
                return object()

        async def public_resolver(host: str, port: int) -> tuple[str, ...]:
            return ("93.184.216.34",)

        stream = await _PublicNetworkBackend(
            resolver=public_resolver,
            backend=RecordingBackend(),  # type: ignore[arg-type]
        ).connect_tcp("example.com", 443)

        self.assertIsNotNone(stream)
        self.assertEqual(calls, ["93.184.216.34"])

    async def test_session_registers_only_stable_web_tool_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeWebProvider()
            session = create_session(
                _settings(Path(directory)),
                AgentHandle(),
                object(),
                search_provider=provider,
                fetch_provider=provider,
            )
        names = [handler.spec.name for handler in session.tools.handlers()]
        self.assertIn("web_search", names)
        self.assertIn("web_fetch", names)
        self.assertFalse(
            any(
                provider in name
                for name in names
                for provider in ("tavily", "exa", "talordata")
            )
        )
        fetch_spec = next(
            handler.spec for handler in session.tools.handlers() if handler.spec.name == "web_fetch"
        )
        self.assertNotIn("url", fetch_spec.parameters["properties"])


class SearchThenFetchClient:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AssistantResponse:
        self.calls += 1
        visible = {tool["function"]["name"] for tool in tools}
        assert {"web_search", "web_fetch"} <= visible
        if self.calls == 1:
            return AssistantResponse(
                None,
                (ToolCall("search-call", "web_search", {"query": "answer source"}),),
            )
        if self.calls == 2:
            search_result = json.loads(messages[-1]["content"])
            assert search_result["trust"] == "untrusted_web_search_results"
            return AssistantResponse(
                None,
                (
                    ToolCall(
                        "fetch-call",
                        "web_fetch",
                        {
                            "search_id": search_result["search_id"],
                            "source_ids": [search_result["sources"][0]["source_id"]],
                            "query": "answer evidence",
                            "max_chars_per_source": 1_000,
                        },
                    ),
                ),
            )
        fetch_result = json.loads(messages[-1]["content"])
        assert fetch_result["trust"] == "untrusted_web_content"
        assert fetch_result["sources"][0]["fetch_method"] == "fake"
        assert fetch_result["sources"][0]["fallback_used"] is True
        return AssistantResponse("Answer with source")


class WebAgentLoopTest(unittest.IsolatedAsyncioTestCase):
    async def test_agent_loop_searches_fetches_and_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            LocalStaticFetcher,
            "fetch",
            side_effect=LocalFetchError("request_failed"),
        ):
            provider = FakeWebProvider()
            client = SearchThenFetchClient()
            handle, runner = await start_agent(
                _settings(Path(directory)),
                client,
                search_provider=provider,
                fetch_provider=provider,
            )
            events = PublicEventAdapter()
            handle.turn_events.subscribe(events)
            handle.submit(UserInput("research"))
            kinds: list[EventKind] = []
            while EventKind.TURN_FINISHED not in kinds:
                kinds.append((await asyncio.wait_for(events.receive(), timeout=2)).kind)
            handle.shutdown()
            await asyncio.wait_for(runner, timeout=2)

        self.assertEqual(client.calls, 3)
        self.assertEqual(provider.search_calls, ["answer source"])
        self.assertEqual(len(provider.fetch_calls), 1)
        self.assertEqual(kinds.count(EventKind.TOOL_CALL), 2)


class TavilyProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_maps_search_and_extract_without_exposing_provider_tools(self) -> None:
        requests: list[dict[str, object]] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            if request.url.path == "/search":
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "title": "Example",
                                "url": "https://example.com/page",
                                "content": "Snippet",
                                "score": 0.8,
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://example.com/page",
                            "raw_content": "Body",
                        }
                    ],
                    "failed_results": [],
                },
            )

        provider = TavilyProvider(
            "tvly-secret", retry_count=0, transport=httpx.MockTransport(respond)
        )
        search = await provider.search("query", max_results=8)
        fetch = await provider.fetch((search[0].url,), query="intent")

        self.assertEqual(search[0].snippet, "Snippet")
        self.assertEqual(fetch[0].content, "Body")
        self.assertEqual(requests[0]["include_raw_content"], False)
        self.assertEqual(requests[1]["urls"], ["https://example.com/page"])

    async def test_authentication_errors_never_contain_the_key(self) -> None:
        for status in (401, 403):
            with self.subTest(status=status):
                provider = TavilyProvider(
                    "tvly-do-not-leak",
                    retry_count=0,
                    transport=httpx.MockTransport(
                        lambda request, status=status: httpx.Response(
                            status, json={"detail": "invalid"}
                        )
                    ),
                )
                with self.assertRaises(WebAuthenticationFailed) as raised:
                    await provider.search("query", max_results=8)
                self.assertNotIn("tvly-do-not-leak", str(raised.exception))

    async def test_rate_limit_is_typed_without_retrying_the_same_key(self) -> None:
        request_count = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(429, headers={"Retry-After": "1"})

        provider = TavilyProvider(
            "tvly-limited",
            retry_count=2,
            transport=httpx.MockTransport(respond),
        )
        with self.assertRaises(WebRateLimited) as raised:
            await provider.search("query", max_results=8)

        self.assertEqual(request_count, 1)
        self.assertEqual(raised.exception.retry_after_seconds, 1)


class ExaProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_maps_search_and_contents_using_official_payload_shapes(self) -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/search":
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "title": "Example",
                                "url": "https://example.com/page",
                                "publishedDate": "2026-07-01",
                                "highlights": ["Relevant excerpt"],
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://example.com/page",
                            "text": "Page body",
                        }
                    ],
                    "statuses": [
                        {"id": "https://example.com/page", "status": "success"}
                    ],
                },
            )

        provider = ExaProvider(
            "exa-secret", retry_count=0, transport=httpx.MockTransport(respond)
        )
        search = await provider.search("query", max_results=8)
        fetch = await provider.fetch((search[0].url,), query="intent")

        search_payload = json.loads(requests[0].content)
        contents_payload = json.loads(requests[1].content)
        self.assertEqual(search[0].snippet, "Relevant excerpt")
        self.assertEqual(search[0].published_at, "2026-07-01")
        self.assertEqual(fetch[0].content, "Page body")
        self.assertEqual(requests[0].headers["x-api-key"], "exa-secret")
        self.assertEqual(search_payload["numResults"], 8)
        self.assertIn("highlights", search_payload["contents"])
        self.assertEqual(contents_payload["urls"], ["https://example.com/page"])
        self.assertIn("text", contents_payload)
        self.assertNotIn("contents", contents_payload)

    async def test_quota_error_never_contains_the_key(self) -> None:
        provider = ExaProvider(
            "exa-do-not-leak",
            retry_count=0,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(402, json={"error": "no credits"})
            ),
        )
        with self.assertRaises(WebQuotaExceeded) as raised:
            await provider.search("query", max_results=8)
        self.assertNotIn("exa-do-not-leak", str(raised.exception))


class TalorDataProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_posts_form_and_maps_official_serp_shape(self) -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "task_id": "task-id",
                        "result": {
                            "organic_results": [
                                {
                                    "title": "Example",
                                    "link": "https://example.com/page",
                                    "description": "Relevant excerpt",
                                    "date": "2026-07-01",
                                }
                            ]
                        },
                    },
                },
            )

        provider = TalorDataProvider(
            "talor-secret", retry_count=0, transport=httpx.MockTransport(respond)
        )
        results = await provider.search("query", max_results=8)

        form = parse_qs(requests[0].content.decode())
        self.assertEqual(requests[0].url.path, "/serp/v1/request")
        self.assertEqual(requests[0].headers["authorization"], "Bearer talor-secret")
        self.assertTrue(
            requests[0].headers["content-type"].startswith(
                "application/x-www-form-urlencoded"
            )
        )
        self.assertEqual(
            form,
            {
                "engine": ["google"],
                "q": ["query"],
                "json": ["1"],
                "num": ["8"],
                "filter": ["1"],
            },
        )
        self.assertEqual(results[0].snippet, "Relevant excerpt")
        self.assertEqual(results[0].published_at, "2026-07-01")

    async def test_maps_business_auth_error_without_leaking_detail(self) -> None:
        provider = TalorDataProvider(
            "talor-do-not-leak",
            retry_count=0,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"code": 401, "data": "talor-do-not-leak invalid"},
                )
            ),
        )
        with self.assertRaises(WebAuthenticationFailed) as raised:
            await provider.search("query", max_results=8)
        self.assertNotIn("talor-do-not-leak", str(raised.exception))


class ProviderConfigurationTest(unittest.TestCase):
    def test_auto_uses_every_configured_provider_key(self) -> None:
        settings = _settings(Path.cwd())
        search_provider, fetch_provider = configured_web_providers(
            settings,
            WebProviderConfig(
                tavily_api_keys=("tavily-one", "tavily-two"),
                exa_api_keys=("exa-one",),
                talordata_api_keys=("talor-one",),
            ),
        )

        self.assertIsInstance(search_provider, RotatingSearchProvider)
        self.assertIsInstance(fetch_provider, RotatingFetchProvider)
        self.assertEqual(search_provider.name, "auto")
        self.assertEqual(fetch_provider.name, "auto")

    def test_selects_exa_without_putting_its_key_in_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "AGENT_WEB_PROVIDER": "exa",
                "EXA_API_KEY": "exa-secret",
                "EXA_API_KEYS": "",
            },
            clear=False,
        ):
            settings = _settings(Path(directory))
            search_provider, fetch_provider = configured_web_providers(settings)

        self.assertIsInstance(search_provider, ExaProvider)
        self.assertIsInstance(fetch_provider, ExaProvider)
        self.assertFalse(hasattr(settings, "exa_api_key"))

    def test_supports_talordata_search_with_exa_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "AGENT_WEB_SEARCH_PROVIDER": "talordata",
                "AGENT_WEB_FETCH_PROVIDER": "exa",
                "TALORDATA_API_KEY": "talor-secret",
                "TALORDATA_API_KEYS": "",
                "EXA_API_KEY": "exa-secret",
                "EXA_API_KEYS": "",
            },
            clear=False,
        ):
            settings = _settings(Path(directory))
            search_provider, fetch_provider = configured_web_providers(settings)

        self.assertIsInstance(search_provider, TalorDataProvider)
        self.assertIsInstance(fetch_provider, ExaProvider)
        self.assertFalse(hasattr(settings, "talordata_api_key"))

    def test_multi_key_json_is_deduplicated_and_creates_rotating_providers(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "AGENT_WEB_PROVIDER": "tavily",
                "TAVILY_API_KEY": "ignored-single",
                "TAVILY_API_KEYS": '[" first ", "second", "first"]',
            },
            clear=False,
        ):
            settings = _settings(Path(directory))
            keys = _api_keys("tavily")
            search_provider, fetch_provider = configured_web_providers(settings)

        self.assertEqual(keys, ("first", "second"))
        self.assertIsInstance(search_provider, RotatingSearchProvider)
        self.assertIsInstance(fetch_provider, RotatingFetchProvider)
        self.assertFalse(hasattr(settings, "tavily_api_keys"))

    def test_rejects_invalid_multi_key_json_without_echoing_it(self) -> None:
        secret = '{"secret":"do-not-echo"}'
        with patch.dict(
            os.environ, {"TAVILY_API_KEYS": secret}, clear=False
        ), self.assertRaises(ValueError) as raised:
            _api_keys("tavily")
        self.assertNotIn(secret, str(raised.exception))

    def test_rejects_talordata_as_fetch_provider(self) -> None:
        with patch.dict(
            os.environ,
            {"AGENT_WEB_FETCH_PROVIDER": "talordata"},
            clear=False,
        ), self.assertRaisesRegex(ValueError, "TalorData 只支持搜索"):
            configured_web_providers(_settings(Path.cwd()))

    def test_rejects_unknown_provider(self) -> None:
        with patch.dict(
            os.environ, {"AGENT_WEB_PROVIDER": "unknown"}, clear=False
        ), self.assertRaisesRegex(ValueError, "tavily、exa 或 talordata"):
            configured_web_providers(_settings(Path.cwd()))


def _settings(workspace: Path) -> Settings:
    return Settings(
        api_key=None,
        model="test-model",
        base_url="http://unused",
        system_prompt="test",
        workspace=workspace,
        shell_enabled=False,
        request_timeout_seconds=1,
        generate_memories=False,
        use_memories=False,
    )


if __name__ == "__main__":
    unittest.main()
