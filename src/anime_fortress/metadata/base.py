from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, ClassVar

import httpx


@dataclass(frozen=True)
class MetadataCandidate:
    provider: str
    external_id: str
    title: str
    year: int | None = None
    anime_type: str | None = None
    source_url: str | None = None


@dataclass
class AnimeMetadata:
    title: str
    alternative_titles: list[str] = field(default_factory=list)
    japanese_title: str | None = None
    release_date: date | None = None
    release_year: int | None = None
    anime_type: str | None = None
    genres: list[str] = field(default_factory=list)
    studio: str | None = None
    rating: float | None = None
    rating_source: str | None = None
    status: str | None = None
    duration_minutes: int | None = None
    age_rating: str | None = None
    synopsis: str | None = None
    broadcast: str | None = None
    episode_count: int | None = None

    def to_fields(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "alternative_titles": self.alternative_titles,
            "japanese_title": self.japanese_title,
            "release_date": self.release_date,
            "release_year": self.release_year,
            "anime_type": self.anime_type,
            "genres": self.genres,
            "studio": self.studio,
            "rating": self.rating,
            "rating_source": self.rating_source,
            "status": self.status,
            "duration_minutes": self.duration_minutes,
            "age_rating": self.age_rating,
            "synopsis": self.synopsis,
            "broadcast": self.broadcast,
            "episode_count": self.episode_count,
        }


@dataclass
class MetadataResult:
    provider: str
    external_id: str
    source_url: str | None
    fetched_at: datetime
    metadata: AnimeMetadata
    raw: dict[str, Any] = field(default_factory=dict)

    def provenance(self) -> dict[str, Any]:
        fields = {
            key: {
                "provider": self.provider,
                "external_id": self.external_id,
                "source_url": self.source_url,
                "fetched_at": self.fetched_at.isoformat(),
            }
            for key, value in self.metadata.to_fields().items()
            if value not in (None, [], "")
        }
        return fields


@dataclass
class MetadataRuntime:
    client: httpx.AsyncClient
    user_agent: str


class MetadataProvider(ABC):
    name: ClassVar[str]
    display_name: ClassVar[str]
    description: ClassVar[str] = ""
    requires_credentials: ClassVar[bool] = False

    def __init__(self, runtime: MetadataRuntime) -> None:
        self.runtime = runtime

    @property
    def client(self) -> httpx.AsyncClient:
        return self.runtime.client

    @abstractmethod
    async def search(self, query: str, *, limit: int = 5) -> list[MetadataCandidate]:
        raise NotImplementedError

    @abstractmethod
    async def fetch(self, external_id: str) -> MetadataResult:
        raise NotImplementedError

    def _result(
        self,
        *,
        external_id: str,
        source_url: str | None,
        metadata: AnimeMetadata,
        raw: dict[str, Any],
    ) -> MetadataResult:
        return MetadataResult(
            provider=self.name,
            external_id=external_id,
            source_url=source_url,
            fetched_at=datetime.now(UTC),
            metadata=metadata,
            raw=raw,
        )
