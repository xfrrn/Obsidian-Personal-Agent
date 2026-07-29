"""Web access domain types, provider contracts, and stable failures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ProviderSearchResult:
    title: str
    url: str
    snippet: str
    score: float | None = None
    published_at: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderFetchResult:
    url: str
    content: str
    provider: str | None = None


class SearchProvider(Protocol):
    name: str

    async def search(
        self, query: str, *, max_results: int
    ) -> tuple[ProviderSearchResult, ...]: ...


class FetchProvider(Protocol):
    name: str

    async def fetch(
        self, urls: tuple[str, ...], *, query: str
    ) -> tuple[ProviderFetchResult, ...]: ...


@dataclass(frozen=True, slots=True)
class SearchSource:
    source_id: str
    title: str
    url: str
    snippet: str
    score: float | None
    published_at: str | None
    domain: str


@dataclass(frozen=True, slots=True)
class SearchRecord:
    search_id: str
    query: str
    sources: tuple[SearchSource, ...]
    created_at: float


@dataclass(frozen=True, slots=True)
class FetchedSource:
    source_id: str
    title: str
    url: str
    domain: str
    published_at: str | None
    content: str
    truncated: bool
    fetch_method: str
    fallback_used: bool


@dataclass(frozen=True, slots=True)
class FetchFailure:
    source_id: str
    error_type: str
    message: str


@dataclass(frozen=True, slots=True)
class WebFetchResult:
    search_id: str
    query: str
    sources: tuple[FetchedSource, ...]
    failures: tuple[FetchFailure, ...]


class WebAccessError(RuntimeError):
    """A safe, provider-neutral error that may be returned to the model."""

    code = "web_access_failed"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "type": self.code,
            "message": str(self),
            "retryable": self.retryable,
        }
        if self.status_code is not None:
            result["status_code"] = self.status_code
        if self.retry_after_seconds is not None:
            result["retry_after_seconds"] = self.retry_after_seconds
        return result


class WebServiceUnavailable(WebAccessError):
    code = "web_service_unavailable"
    retryable = True


class WebAuthenticationFailed(WebAccessError):
    code = "web_authentication_failed"


class WebRateLimited(WebAccessError):
    code = "web_rate_limited"
    retryable = True


class WebQuotaExceeded(WebAccessError):
    code = "web_quota_exceeded"


class WebSearchTimeout(WebAccessError):
    code = "web_search_timeout"
    retryable = True


class WebFetchTimeout(WebAccessError):
    code = "web_fetch_timeout"
    retryable = True


class WebFetchFailed(WebAccessError):
    code = "web_fetch_failed"
    retryable = True


class SearchRecordExpired(WebAccessError):
    code = "search_record_expired"


class SourceIdInvalid(WebAccessError):
    code = "source_id_invalid"


class WebContentEmpty(WebAccessError):
    code = "web_content_empty"


class ProviderResponseInvalid(WebAccessError):
    code = "provider_response_invalid"


class WebLimitExceeded(WebAccessError):
    code = "web_limit_exceeded"


class WebRequestInvalid(WebAccessError):
    code = "web_request_invalid"
