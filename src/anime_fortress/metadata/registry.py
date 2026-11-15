from __future__ import annotations

import logging

import httpx

from anime_fortress.config import Settings
from anime_fortress.errors import MetadataError
from anime_fortress.metadata.base import MetadataProvider, MetadataRuntime
from anime_fortress.metadata.providers.anilist import AniListProvider
from anime_fortress.metadata.providers.jikan import JikanProvider

logger = logging.getLogger(__name__)


class MetadataRegistry:
    def __init__(
        self,
        providers: list[MetadataProvider],
        *,
        default_order: list[str] | None = None,
    ) -> None:
        self._providers = {provider.name: provider for provider in providers}
        self.default_order = default_order or list(self._providers)

    def names(self) -> list[str]:
        return list(self._providers)

    def get(self, name: str) -> MetadataProvider:
        provider = self._providers.get(name.strip().lower())
        if provider is None:
            raise MetadataError(
                f"Unknown metadata provider '{name}'. Available: {', '.join(self.names())}"
            )
        return provider

    def ordered(self) -> list[MetadataProvider]:
        ordered = [self.get(name) for name in self.default_order if name in self._providers]
        for _name, provider in self._providers.items():
            if provider not in ordered:
                ordered.append(provider)
        return ordered

    async def search_all(self, query: str, *, limit: int = 5) -> list[tuple[str, str, str]]:
        """Return (provider, external_id, title) tuples across configured providers."""
        results: list[tuple[str, str, str]] = []
        for provider in self.ordered():
            try:
                candidates = await provider.search(query, limit=limit)
            except MetadataError as exc:
                logger.warning("Metadata search failed on %s: %s", provider.name, exc)
                continue
            for candidate in candidates:
                results.append((candidate.provider, candidate.external_id, candidate.title))
        return results


def default_metadata_registry(
    settings: Settings, client: httpx.AsyncClient | None = None
) -> tuple[MetadataRegistry, httpx.AsyncClient, bool]:
    owned = client is None
    http_client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(settings.http_timeout_seconds), follow_redirects=True
    )
    runtime = MetadataRuntime(client=http_client, user_agent=settings.user_agent)
    available: list[MetadataProvider] = [JikanProvider(runtime), AniListProvider(runtime)]
    wanted = [name.strip().lower() for name in settings.metadata_providers if name.strip()]
    providers = [provider for provider in available if not wanted or provider.name in wanted]
    if not providers:
        providers = available
    registry = MetadataRegistry(
        providers, default_order=wanted or ["jikan", "anilist"]
    )
    return registry, http_client, owned
