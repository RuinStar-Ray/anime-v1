from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import TypeVar

from anime_fortress.errors import SourceError
from anime_fortress.sources.adapters.direct_url import DirectUrlAdapter
from anime_fortress.sources.adapters.local_library import LocalLibraryAdapter
from anime_fortress.sources.adapters.rss_feed import RssFeedAdapter
from anime_fortress.sources.base import SourceAdapter

logger = logging.getLogger(__name__)
AdapterT = TypeVar("AdapterT", bound=SourceAdapter)

ENTRY_POINT_GROUP = "anime_fortress.sources"


class SourceRegistry:
    def __init__(self, adapters: list[SourceAdapter] | None = None) -> None:
        self._adapters: dict[str, SourceAdapter] = {}
        for adapter in adapters or []:
            self.register(adapter)

    def register(self, adapter: SourceAdapter) -> None:
        name = adapter.name.strip().lower()
        if not name:
            raise SourceError("Adapter name cannot be empty")
        self._adapters[name] = adapter

    def get(self, name: str) -> SourceAdapter:
        adapter = self._adapters.get(name.strip().lower())
        if adapter is None:
            raise SourceError(
                f"Source adapter '{name}' is not installed. "
                f"Available adapters: {', '.join(self.names()) or 'none'}"
            )
        return adapter

    def names(self) -> list[str]:
        return sorted(self._adapters)

    def all(self) -> list[SourceAdapter]:
        return [self._adapters[name] for name in self.names()]

    def load_entry_point_plugins(self) -> None:
        try:
            plugins = entry_points(group=ENTRY_POINT_GROUP)
        except Exception as exc:  # pragma: no cover - platform-specific metadata failures
            logger.warning("Could not load source adapter entry points: %s", exc)
            return
        for plugin in plugins:
            try:
                adapter = plugin.load()()
                if not isinstance(adapter, SourceAdapter):
                    raise TypeError("entry point did not return a SourceAdapter instance")
                self.register(adapter)
                logger.info("Loaded custom source adapter: %s", adapter.name)
            except Exception as exc:  # pragma: no cover - plugin failures are environment-specific
                logger.error("Failed to load source adapter plugin %s: %s", plugin.name, exc)


def default_source_registry(load_plugins: bool = True) -> SourceRegistry:
    registry = SourceRegistry([DirectUrlAdapter(), LocalLibraryAdapter(), RssFeedAdapter()])
    if load_plugins:
        registry.load_entry_point_plugins()
    return registry
