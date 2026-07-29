"""Select web providers after the shared .env loader has run."""

from __future__ import annotations

from dataclasses import dataclass
import json

from agent.config.loader import env_value
from agent.config.settings import Settings
from agent.web_access.exa import ExaProvider
from agent.web_access.key_pool import RotatingFetchProvider, RotatingSearchProvider
from agent.web_access.talordata import TalorDataProvider
from agent.web_access.tavily import TavilyProvider
from agent.web_access.types import FetchProvider, SearchProvider


@dataclass(frozen=True, slots=True)
class WebProviderConfig:
    search_provider: str = "tavily"
    fetch_provider: str = "tavily"
    tavily_api_keys: tuple[str, ...] = ()
    exa_api_keys: tuple[str, ...] = ()
    talordata_api_keys: tuple[str, ...] = ()

    def keys_for(self, provider: str) -> tuple[str, ...]:
        if provider == "tavily":
            return self.tavily_api_keys
        if provider == "exa":
            return self.exa_api_keys
        if provider == "talordata":
            return self.talordata_api_keys
        raise ValueError("provider 必须是 tavily、exa 或 talordata")

    def key_status(self) -> dict[str, bool]:
        return {
            "tavily": bool(self.tavily_api_keys),
            "exa": bool(self.exa_api_keys),
            "talordata": bool(self.talordata_api_keys),
        }


def web_provider_config_from_env() -> WebProviderConfig:
    default_name = env_value("AGENT_WEB_PROVIDER", "tavily").strip().casefold()
    return WebProviderConfig(
        search_provider=env_value("AGENT_WEB_SEARCH_PROVIDER", default_name)
        .strip()
        .casefold(),
        fetch_provider=env_value("AGENT_WEB_FETCH_PROVIDER", default_name)
        .strip()
        .casefold(),
        tavily_api_keys=_api_keys("tavily"),
        exa_api_keys=_api_keys("exa"),
        talordata_api_keys=_api_keys("talordata"),
    )


def configured_web_providers(
    settings: Settings,
    config: WebProviderConfig | None = None,
) -> tuple[SearchProvider | None, FetchProvider | None]:
    config = config or web_provider_config_from_env()
    search_name = config.search_provider
    fetch_name = config.fetch_provider
    if search_name not in {"tavily", "exa", "talordata"}:
        raise ValueError(
            "AGENT_WEB_SEARCH_PROVIDER 只能是 tavily、exa 或 talordata"
        )
    if fetch_name not in {"tavily", "exa"}:
        raise ValueError(
            "AGENT_WEB_FETCH_PROVIDER 只能是 tavily 或 exa；TalorData 只支持搜索"
        )

    search_candidates = _search_providers(
        search_name, settings, config.keys_for(search_name)
    )
    fetch_candidates = _fetch_providers(
        fetch_name, settings, config.keys_for(fetch_name)
    )
    # Runtime 要求搜索和读取成对注册；缺少任一密钥时沿用原有的禁用行为。
    if not search_candidates or not fetch_candidates:
        return None, None
    search_provider: SearchProvider = (
        search_candidates[0]
        if len(search_candidates) == 1
        else RotatingSearchProvider(search_candidates)
    )
    fetch_provider: FetchProvider = (
        fetch_candidates[0]
        if len(fetch_candidates) == 1
        else RotatingFetchProvider(fetch_candidates)
    )
    return search_provider, fetch_provider


def _search_providers(
    name: str, settings: Settings, keys: tuple[str, ...]
) -> tuple[SearchProvider, ...]:
    if name == "tavily":
        return tuple(TavilyProvider.from_settings(key, settings) for key in keys)
    if name == "exa":
        return tuple(ExaProvider.from_settings(key, settings) for key in keys)
    return tuple(TalorDataProvider.from_settings(key, settings) for key in keys)


def _fetch_providers(
    name: str, settings: Settings, keys: tuple[str, ...]
) -> tuple[FetchProvider, ...]:
    if name == "tavily":
        return tuple(TavilyProvider.from_settings(key, settings) for key in keys)
    return tuple(ExaProvider.from_settings(key, settings) for key in keys)


def _api_keys(provider: str) -> tuple[str, ...]:
    prefix = provider.upper()
    list_name = f"{prefix}_API_KEYS"
    encoded = env_value(list_name).strip()
    if not encoded:
        single = env_value(f"{prefix}_API_KEY").strip()
        return (single,) if single else ()
    try:
        decoded = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{list_name} 必须是 JSON 字符串数组") from exc
    if not isinstance(decoded, list) or any(
        not isinstance(key, str) or not key.strip() for key in decoded
    ):
        raise ValueError(f"{list_name} 必须是非空字符串组成的 JSON 数组")
    # 重复 Key 不应在一次故障转移中被再次请求。
    return tuple(dict.fromkeys(key.strip() for key in decoded))
