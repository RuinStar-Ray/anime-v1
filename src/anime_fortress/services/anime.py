from __future__ import annotations

from anime_fortress.db.models import Anime, Channel, Source
from anime_fortress.db.repositories import (
    AnimeRepository,
    ChannelRepository,
    EpisodeRepository,
    SourceRepository,
)
from anime_fortress.db.session import Database
from anime_fortress.errors import ValidationError
from anime_fortress.languages import canonical_language
from anime_fortress.naming import normalize_quality


class AnimeService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, anime_id: int) -> Anime | None:
        async with self.db.session() as session:
            return await AnimeRepository(session).get_with_relations(anime_id)

    async def list(self) -> list[Anime]:
        async with self.db.session() as session:
            return await AnimeRepository(session).list()

    async def set_channel(self, anime_id: int, channel_id: int) -> Anime:
        async with self.db.session() as session:
            anime = await AnimeRepository(session).get(anime_id)
            if anime is None:
                raise ValidationError(f"Anime {anime_id} was not found")
            channel = await ChannelRepository(session).get(channel_id)
            if channel is None:
                raise ValidationError(f"Channel {channel_id} was not found")
            anime.channel_id = channel_id
            return anime

    async def set_source(self, anime_id: int, source_id: int) -> Anime:
        async with self.db.session() as session:
            anime = await AnimeRepository(session).get(anime_id)
            if anime is None:
                raise ValidationError(f"Anime {anime_id} was not found")
            source = await SourceRepository(session).get(source_id)
            if source is None:
                raise ValidationError(f"Source {source_id} was not found")
            anime.source_id = source_id
            return anime

    async def set_languages(self, anime_id: int, values: list[str]) -> Anime:
        if not values:
            raise ValidationError("Provide at least one preferred audio language")
        normalized: list[str] = []
        for value in values:
            language = canonical_language(value)
            if language not in normalized:
                normalized.append(language)
        async with self.db.session() as session:
            anime = await AnimeRepository(session).get(anime_id)
            if anime is None:
                raise ValidationError(f"Anime {anime_id} was not found")
            anime.preferred_languages = normalized
            return anime

    async def set_qualities(self, anime_id: int, values: list[str]) -> Anime:
        if not values:
            raise ValidationError("Provide at least one preferred quality")
        normalized: list[str] = []
        for value in values:
            quality = normalize_quality(value)
            if quality not in normalized:
                normalized.append(quality)
        async with self.db.session() as session:
            anime = await AnimeRepository(session).get(anime_id)
            if anime is None:
                raise ValidationError(f"Anime {anime_id} was not found")
            anime.preferred_qualities = normalized
            return anime

    async def set_extra_audio(self, anime_id: int, include: bool) -> Anime:
        async with self.db.session() as session:
            anime = await AnimeRepository(session).get(anime_id)
            if anime is None:
                raise ValidationError(f"Anime {anime_id} was not found")
            anime.include_extra_audio = include
            return anime

    async def episode_count(self, anime_id: int) -> int:
        async with self.db.session() as session:
            return len(await EpisodeRepository(session).list_for_anime(anime_id))

    async def channels(self) -> list[Channel]:
        async with self.db.session() as session:
            return await ChannelRepository(session).list()

    async def sources(self) -> list[Source]:
        async with self.db.session() as session:
            return await SourceRepository(session).list()
