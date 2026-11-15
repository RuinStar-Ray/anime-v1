from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from anime_fortress.db.base import utcnow
from anime_fortress.db.models import Anime
from anime_fortress.db.repositories import AnimeRepository
from anime_fortress.errors import MetadataError, ValidationError
from anime_fortress.languages import canonical_language
from anime_fortress.metadata.base import MetadataCandidate, MetadataResult
from anime_fortress.metadata.registry import MetadataRegistry
from anime_fortress.naming import normalize_quality

logger = logging.getLogger(__name__)

EDITABLE_FIELDS = {
    "title",
    "alternative_titles",
    "japanese_title",
    "release_date",
    "release_year",
    "anime_type",
    "genres",
    "studio",
    "rating",
    "rating_source",
    "status",
    "duration_minutes",
    "age_rating",
    "synopsis",
    "broadcast",
    "episode_count",
}
LIST_FIELDS = {"alternative_titles", "genres"}


class MetadataService:
    def __init__(self, registry: MetadataRegistry) -> None:
        self.registry = registry

    async def search(self, query: str, *, limit: int = 5) -> list[MetadataCandidate]:
        results: list[MetadataCandidate] = []
        for provider in self.registry.ordered():
            try:
                results.extend(await provider.search(query, limit=limit))
            except MetadataError as exc:
                logger.warning("Metadata search on %s failed: %s", provider.name, exc)
        return results

    async def fetch(self, provider: str, external_id: str) -> MetadataResult:
        return await self.registry.get(provider).fetch(external_id)

    async def create_anime(
        self,
        session: AsyncSession,
        *,
        title: str,
        source_id: int | None,
        channel_id: int | None,
        result: MetadataResult | None = None,
    ) -> Anime:
        repo = AnimeRepository(session)
        values: dict[str, Any] = {
            "title": title.strip(),
            "source_id": source_id,
            "channel_id": channel_id,
        }
        if result:
            values.update(result.metadata.to_fields())
            values["title"] = result.metadata.title or title.strip()
            values["metadata_provider"] = result.provider
            values["metadata_external_id"] = result.external_id
            values["metadata_fetched_at"] = result.fetched_at
            values["metadata_provenance"] = result.provenance()
        if not values["title"]:
            raise ValidationError("Anime title cannot be empty")
        return await repo.create(**values)

    async def apply(self, session: AsyncSession, anime: Anime, result: MetadataResult) -> Anime:
        repo = AnimeRepository(session)
        return await repo.update_metadata(
            anime,
            fields=result.metadata.to_fields(),
            provider=result.provider,
            external_id=result.external_id,
            provenance=result.provenance(),
            fetched_at=result.fetched_at,
        )

    async def correct(
        self, session: AsyncSession, anime: Anime, field_name: str, raw_value: str
    ) -> Anime:
        field = field_name.strip().lower()
        if field not in EDITABLE_FIELDS:
            raise ValidationError(
                f"Field '{field_name}' is not editable. Editable: {', '.join(sorted(EDITABLE_FIELDS))}"
            )
        value: Any = raw_value.strip()
        if field in LIST_FIELDS:
            parts = [part.strip() for part in value.split(",") if part.strip()]
            value = parts if parts else []
        elif field == "release_date":
            if not value:
                value = None
            else:
                try:
                    value = date.fromisoformat(value)
                except ValueError as exc:
                    raise ValidationError("release_date must use YYYY-MM-DD") from exc
        elif field in {"release_year", "duration_minutes", "episode_count"}:
            if not value:
                value = None
            else:
                try:
                    value = int(value)
                except ValueError as exc:
                    raise ValidationError(f"{field} must be an integer") from exc
                if value < 0:
                    raise ValidationError(f"{field} cannot be negative")
        elif field == "rating":
            if not value:
                value = None
            else:
                try:
                    value = float(value)
                except ValueError as exc:
                    raise ValidationError("rating must be a number") from exc
        else:
            value = value or None
        repo = AnimeRepository(session)
        return await repo.update_metadata(
            anime,
            fields={field: value},
            provenance={
                field: {
                    "provider": "manual",
                    "fetched_at": datetime.now(UTC).isoformat(),
                }
            },
            fetched_at=utcnow(),
        )


def normalize_language_list(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        language = canonical_language(value)
        if language not in normalized:
            normalized.append(language)
    return normalized


def normalize_quality_list(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        quality = normalize_quality(value)
        if quality not in normalized:
            normalized.append(quality)
    return normalized
