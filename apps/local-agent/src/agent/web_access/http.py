"""Shared POST transport for web-access providers."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from agent.web_access.types import (
    ProviderResponseInvalid,
    WebAccessError,
    WebAuthenticationFailed,
    WebQuotaExceeded,
    WebRateLimited,
    WebServiceUnavailable,
)


_LOGGER = logging.getLogger(__name__)


class HttpApiClient:
    """Keep retry, timeout, redaction, and status handling identical across providers."""

    def __init__(
        self,
        provider: str,
        base_url: str,
        headers: dict[str, str],
        *,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        retry_count: int,
        quota_statuses: frozenset[int],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._provider = provider
        self._base_url = base_url.rstrip("/")
        self._headers = headers
        self._connect_timeout = connect_timeout_seconds
        self._read_timeout = read_timeout_seconds
        self._retry_count = retry_count
        self._quota_statuses = quota_statuses
        self._transport = transport

    async def post_json(
        self,
        path: str,
        payload: dict[str, object],
        timeout_error: type[WebAccessError],
    ) -> dict[str, Any]:
        return await self._post(path, payload, timeout_error, form_encoded=False)

    async def post_form(
        self,
        path: str,
        payload: dict[str, object],
        timeout_error: type[WebAccessError],
    ) -> dict[str, Any]:
        return await self._post(path, payload, timeout_error, form_encoded=True)

    async def _post(
        self,
        path: str,
        payload: dict[str, object],
        timeout_error: type[WebAccessError],
        *,
        form_encoded: bool,
    ) -> dict[str, Any]:
        timeout = httpx.Timeout(
            connect=self._connect_timeout,
            read=self._read_timeout,
            write=self._connect_timeout,
            pool=self._connect_timeout,
        )
        started_at = time.monotonic()
        async with httpx.AsyncClient(
            timeout=timeout, transport=self._transport, follow_redirects=False
        ) as client:
            for attempt in range(self._retry_count + 1):
                try:
                    if form_encoded:
                        response = await client.post(
                            f"{self._base_url}/{path}",
                            headers=self._headers,
                            data=payload,
                        )
                    else:
                        response = await client.post(
                            f"{self._base_url}/{path}",
                            headers=self._headers,
                            json=payload,
                        )
                except httpx.TimeoutException as exc:
                    if attempt < self._retry_count:
                        await asyncio.sleep(_backoff(attempt))
                        continue
                    raise timeout_error(f"{path} 请求超时。") from exc
                except httpx.RequestError as exc:
                    if attempt < self._retry_count:
                        await asyncio.sleep(_backoff(attempt))
                        continue
                    raise WebServiceUnavailable("无法连接 Web Provider。") from exc

                status = response.status_code
                if status == 200:
                    try:
                        decoded = response.json()
                    except ValueError as exc:
                        raise ProviderResponseInvalid(
                            "Web Provider 未返回 JSON。", status_code=status
                        ) from exc
                    if not isinstance(decoded, dict):
                        raise ProviderResponseInvalid(
                            "Web Provider 未返回 JSON 对象。", status_code=status
                        )
                    _LOGGER.info(
                        "web.provider.completed",
                        extra={
                            "provider": self._provider,
                            "operation": path,
                            "duration_ms": round(
                                (time.monotonic() - started_at) * 1000
                            ),
                            "http_status": status,
                            "retry_count": attempt,
                        },
                    )
                    return decoded
                if status in {401, 403}:
                    raise WebAuthenticationFailed(
                        "Web Provider API Key 无效或无权访问。", status_code=status
                    )
                if status in self._quota_statuses:
                    raise WebQuotaExceeded(
                        "Web Provider 配额已用尽。", status_code=status
                    )
                if status == 429:
                    retry_after = _retry_after(response)
                    raise WebRateLimited(
                        "Web Provider 请求频率受限。",
                        status_code=status,
                        retry_after_seconds=retry_after,
                    )
                if (status == 408 or status >= 500) and attempt < self._retry_count:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                if status == 408 or status >= 500:
                    raise WebServiceUnavailable(
                        "Web Provider 暂时不可用。", status_code=status
                    )
                raise ProviderResponseInvalid(
                    "Web Provider 拒绝了请求。", status_code=status
                )
        raise AssertionError("unreachable")


def _backoff(attempt: int) -> float:
    return 0.5 * (2**attempt)


def _retry_after(response: httpx.Response) -> int | None:
    try:
        value = int(response.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return max(0, value)
