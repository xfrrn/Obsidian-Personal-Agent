"""Bounded static HTML fetching with DNS-pinned SSRF protection."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Awaitable, Callable, Iterable
from dataclasses import dataclass
import ipaddress
import logging
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpcore
import httpx
from trafilatura import extract


MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 2_000_000
MIN_CONTENT_CHARS = 200
MAX_LOCAL_CONTENT_CHARS = 12_001
_MAX_URL_CHARS = 2_048
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_LOGGER = logging.getLogger(__name__)


class LocalFetchError(RuntimeError):
    def __init__(self, reason: str, *, status_code: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class LocalFetchResult:
    content: str
    status_code: int


class LocalStaticFetcher:
    def __init__(
        self,
        *,
        connect_timeout_seconds: float = 5,
        read_timeout_seconds: float = 8,
        total_timeout_seconds: float = 12,
        max_redirects: int = MAX_REDIRECTS,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        min_content_chars: int = MIN_CONTENT_CHARS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=connect_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        self._total_timeout = total_timeout_seconds
        self._max_redirects = max_redirects
        self._max_response_bytes = max_response_bytes
        self._min_content_chars = min_content_chars
        self._transport = transport

    async def fetch(self, url: str, max_chars: int) -> LocalFetchResult:
        started_at = time.monotonic()
        domain: str | None = None
        status_code: int | None = None
        try:
            async with asyncio.timeout(self._total_timeout):
                current_url, domain = safe_web_url(url)
                transport = self._transport or _PinnedTransport()
                async with httpx.AsyncClient(
                    transport=transport,
                    timeout=self._timeout,
                    follow_redirects=False,
                    headers={
                        "Accept": "text/html",
                        "User-Agent": "Obsidian-Personal-Agent/0.1",
                    },
                ) as client:
                    for redirect_count in range(self._max_redirects + 1):
                        async with client.stream("GET", current_url) as response:
                            status_code = response.status_code
                            if status_code in _REDIRECT_STATUSES:
                                location = response.headers.get("Location")
                                if not location:
                                    raise LocalFetchError(
                                        "redirect_missing_location",
                                        status_code=status_code,
                                    )
                                if redirect_count >= self._max_redirects:
                                    raise LocalFetchError(
                                        "too_many_redirects", status_code=status_code
                                    )
                                current_url, domain = safe_web_url(
                                    urljoin(current_url, location)
                                )
                                continue
                            if not 200 <= status_code < 300:
                                raise LocalFetchError(
                                    "http_status", status_code=status_code
                                )
                            content_type = response.headers.get("Content-Type", "")
                            if content_type.partition(";")[0].strip().casefold() != "text/html":
                                raise LocalFetchError(
                                    "unsupported_content_type", status_code=status_code
                                )
                            body = await self._read_body(response)
                            try:
                                markdown = await asyncio.to_thread(
                                    extract,
                                    body,
                                    url=current_url,
                                    output_format="markdown",
                                    include_comments=False,
                                    include_links=True,
                                )
                            except Exception as exc:
                                raise LocalFetchError(
                                    "extraction_failed", status_code=status_code
                                ) from exc
                            content = (markdown or "").strip()
                            if not content:
                                raise LocalFetchError(
                                    "content_empty", status_code=status_code
                                )
                            if len(content) < self._min_content_chars:
                                raise LocalFetchError(
                                    "content_too_short", status_code=status_code
                                )
                            content = content[: min(max_chars + 1, MAX_LOCAL_CONTENT_CHARS)]
                            _LOGGER.info(
                                "web.fetch.local_completed",
                                extra={
                                    "domain": domain,
                                    "http_status": status_code,
                                    "duration_ms": _elapsed_ms(started_at),
                                    "content_chars": len(content),
                                    "fetch_method": "local",
                                    "fallback_used": False,
                                },
                            )
                            return LocalFetchResult(content, status_code)
                    raise AssertionError("unreachable")
        except LocalFetchError as exc:
            status_code = exc.status_code or status_code
            error = exc
        except (TimeoutError, httpx.TimeoutException, httpcore.TimeoutException):
            error = LocalFetchError("timeout", status_code=status_code)
        except Exception:
            error = LocalFetchError("request_failed", status_code=status_code)
        _LOGGER.warning(
            "web.fetch.local_failed",
            extra={
                "domain": domain,
                "http_status": status_code,
                "duration_ms": _elapsed_ms(started_at),
                "content_chars": 0,
                "fetch_method": "local",
                "fallback_used": True,
                "failure_reason": error.reason,
            },
        )
        raise error

    async def _read_body(self, response: httpx.Response) -> bytes:
        try:
            declared_size = int(response.headers.get("Content-Length", ""))
        except ValueError:
            declared_size = 0
        if declared_size > self._max_response_bytes:
            raise LocalFetchError(
                "response_too_large", status_code=response.status_code
            )
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self._max_response_bytes:
                raise LocalFetchError(
                    "response_too_large", status_code=response.status_code
                )
            chunks.append(chunk)
        return b"".join(chunks)


def safe_web_url(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or not value or len(value) > _MAX_URL_CHARS:
        raise LocalFetchError("invalid_url")
    try:
        parts = urlsplit(value)
        port = parts.port
        host = parts.hostname
    except ValueError as exc:
        raise LocalFetchError("invalid_url") from exc
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not host:
        raise LocalFetchError("unsupported_scheme")
    if parts.username is not None or parts.password is not None:
        raise LocalFetchError("embedded_credentials")
    expected_port = 80 if scheme == "http" else 443
    if port is not None and port != expected_port:
        raise LocalFetchError("nonstandard_port")
    try:
        domain = host.encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise LocalFetchError("invalid_url") from exc
    if domain == "localhost" or domain.endswith(
        (".localhost", ".local", ".internal", ".home", ".lan")
    ):
        raise LocalFetchError("private_network")
    try:
        address = ipaddress.ip_address(domain)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise LocalFetchError("private_network")
    netloc = f"[{domain}]" if ":" in domain else domain
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, "")), domain


class _PublicNetworkBackend(httpcore.AsyncNetworkBackend):
    """Resolve once, reject non-public answers, then connect to the checked IP."""

    def __init__(
        self,
        resolver: Callable[[str, int], Awaitable[tuple[str, ...]]] | None = None,
        backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._resolver = resolver or _resolve_addresses
        self._backend = backend or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = await self._resolver(host, port)
        if not addresses or any(not ipaddress.ip_address(item).is_global for item in addresses):
            raise LocalFetchError("private_network")
        last_error: Exception | None = None
        for address in addresses:
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise LocalFetchError("unsupported_scheme")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class _PinnedTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=1,
            max_keepalive_connections=1,
            network_backend=_PublicNetworkBackend(),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._pool.handle_async_request(
            httpcore.Request(
                method=request.method,
                url=httpcore.URL(
                    scheme=request.url.raw_scheme,
                    host=request.url.raw_host,
                    port=request.url.port,
                    target=request.url.raw_path,
                ),
                headers=request.headers.raw,
                content=request.stream,
                extensions=request.extensions,
            )
        )
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_CoreResponseStream(response.stream),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


class _CoreResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: AsyncIterable[bytes]) -> None:
        self._stream = stream

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()  # type: ignore[attr-defined]


async def _resolve_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        results = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise httpcore.ConnectError("DNS resolution failed") from exc
    return tuple(dict.fromkeys(result[4][0] for result in results))


def _elapsed_ms(started_at: float) -> int:
    return round((time.monotonic() - started_at) * 1000)
