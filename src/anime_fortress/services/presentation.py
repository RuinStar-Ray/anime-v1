from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from anime_fortress.config import Settings
from anime_fortress.db.models import Anime, Episode, ProcessedFile
from anime_fortress.db.repositories import AnimeRepository, TemplateRepository
from anime_fortress.db.session import Database
from anime_fortress.errors import ValidationError
from anime_fortress.languages import dedupe_languages, order_languages
from anime_fortress.telegram.captions import (
    announcement_context,
    episode_caption_context,
)
from anime_fortress.templates import TemplateEngine

logger = logging.getLogger(__name__)


@dataclass
class RenderedAnnouncement:
    text: str
    poster_path: Path | None
    template_id: int | None
    context: dict[str, object]
    warnings: list[str] = field(default_factory=list)


@dataclass
class RenderedCaption:
    text: str
    template_id: int | None


def _quality_sort(label: str) -> tuple[int, str]:
    digits = "".join(character for character in label if character.isdigit())
    return (int(digits) if digits else 0, label)


class PresentationService:
    def __init__(self, db: Database, settings: Settings, engine: TemplateEngine) -> None:
        self.db = db
        self.settings = settings
        self.engine = engine

    async def _availability(
        self, anime_id: int
    ) -> tuple[int | None, int | None, list[str], list[str]]:
        async with self.db.session() as session:
            anime = await AnimeRepository(session).get_full(anime_id)
            if anime is None:
                raise ValidationError(f"Anime {anime_id} was not found")
            result = await session.execute(
                select(ProcessedFile)
                .join(Episode, Episode.id == ProcessedFile.episode_id)
                .where(Episode.anime_id == anime_id)
                .options(selectinload(ProcessedFile.audio_tracks))
            )
            files = list(result.scalars().all())
            audio: list[str] = []
            for processed in files:
                for track in processed.audio_tracks:
                    audio.append(track.language)
            qualities = sorted({item.resolution_label for item in files}, key=_quality_sort)
            season_count = len(anime.seasons) or None
            episode_count = sum(len(season.episodes) for season in anime.seasons) or None
        ordered_audio = order_languages(dedupe_languages(audio), self.settings.preferred_languages)
        return season_count, episode_count, ordered_audio, qualities

    async def render_announcement(self, anime_id: int) -> RenderedAnnouncement:
        async with self.db.session() as session:
            anime = await AnimeRepository(session).get_full(anime_id)
            if anime is None:
                raise ValidationError(f"Anime {anime_id} was not found")
            if anime.channel is None:
                raise ValidationError("Assign a destination channel with /setchannel before previewing")
            channel = anime.channel
            template = anime.announcement_template
            if template is None:
                template = await TemplateRepository(session).get_by_name("default-announcement")
            template_id = template.id if template else None
            body = template.body if template else self.settings.announcement_template
            if template is None:
                self.engine.validate(body)
            poster_path = None
            if anime.poster and Path(anime.poster.path).exists():
                poster_path = Path(anime.poster.path)
            season_count, episode_count, audio, qualities = await self._availability(anime_id)
            context = announcement_context(
                anime,
                channel,
                settings=self.settings,
                seasons_count=season_count,
                audio_languages=audio,
                available_qualities=qualities,
                episode_count_discovered=episode_count,
            )
            text = self.engine.render(body, context)
        warnings: list[str] = []
        if poster_path is None:
            warnings.append("No custom poster is set; publishing will be blocked until /setposter.")
        if not audio:
            warnings.append("No processed audio tracks yet; audio fields show N/A.")
        if not qualities:
            warnings.append("No processed qualities yet; quality fields show N/A.")
        if len(text) > self.settings.max_caption_length:
            warnings.append(
                "Text exceeds the Telegram caption limit; the poster will be sent with a short "
                "caption and full details in a connected message."
            )
        if len(text) > self.settings.max_message_length:
            warnings.append("Text exceeds one Telegram message and will be split into plain-text parts.")
        return RenderedAnnouncement(
            text=text,
            poster_path=poster_path,
            template_id=template_id,
            context=context,
            warnings=warnings,
        )

    async def render_caption(self, processed_file_id: int) -> RenderedCaption:
        async with self.db.session() as session:
            result = await session.execute(
                select(ProcessedFile)
                .where(ProcessedFile.id == processed_file_id)
                .options(
                    selectinload(ProcessedFile.audio_tracks),
                    selectinload(ProcessedFile.episode).selectinload(Episode.season),
                    selectinload(ProcessedFile.episode)
                    .selectinload(Episode.anime)
                    .selectinload(Anime.channel),
                )
            )
            processed = result.scalar_one_or_none()
            if processed is None:
                raise ValidationError(f"Processed file {processed_file_id} was not found")
            episode = processed.episode
            anime = episode.anime
            if anime.channel is None:
                raise ValidationError("Anime has no destination channel")
            channel = anime.channel
            template = (
                await TemplateRepository(session).get(channel.caption_template_id)
                if channel.caption_template_id
                else None
            )
            if template is None:
                template = await TemplateRepository(session).get_by_name("default-caption")
            body = template.body if template else self.settings.caption_template
            template_id = template.id if template else None
            context = episode_caption_context(
                anime=anime,
                channel=channel,
                season=episode.season,
                episode=episode,
                processed=processed,
                filename=processed.filename,
                settings=self.settings,
            )
            text = self.engine.render_caption(body, context)
        return RenderedCaption(text=text, template_id=template_id)
