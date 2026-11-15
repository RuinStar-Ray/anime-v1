from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from anime_fortress.config import Settings
from anime_fortress.db.enums import EpisodeStatus, ProcessedStatus, TaskKind
from anime_fortress.db.repositories import (
    AnimeRepository,
    EpisodeRepository,
    ProcessedFileRepository,
    QueueRepository,
    SourceMediaRepository,
    UploadRepository,
)
from anime_fortress.db.session import Database
from anime_fortress.errors import PermanentTaskError
from anime_fortress.languages import build_audio_label
from anime_fortress.media.planning import build_plans
from anime_fortress.media.processor import MediaProcessor, build_source_input, sha256_file
from anime_fortress.naming import render_filename
from anime_fortress.services.priorities import publish_priority

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[str, int], Awaitable[None]]


@dataclass
class ProcessSummary:
    episode_id: int
    planned_qualities: list[str] = field(default_factory=list)
    processed_file_ids: list[int] = field(default_factory=list)
    queued_uploads: int = 0
    skipped_uploads: int = 0


class MediaService:
    def __init__(self, db: Database, settings: Settings, processor: MediaProcessor) -> None:
        self.db = db
        self.settings = settings
        self.processor = processor

    async def process_episode(
        self,
        episode_id: int,
        *,
        progress: ProgressCallback | None = None,
        task_id: int | None = None,
    ) -> ProcessSummary:
        async with self.db.session() as session:
            episodes = EpisodeRepository(session)
            episode = await episodes.get_with_files(episode_id)
            if episode is None:
                raise PermanentTaskError(f"Episode {episode_id} was not found")
            if episode.status == EpisodeStatus.SKIPPED:
                raise PermanentTaskError("Episode was skipped by an administrator")
            anime = await AnimeRepository(session).get_with_relations(episode.anime_id)
            if anime is None:
                raise PermanentTaskError("Anime record was not found")
            if anime.channel is None:
                raise PermanentTaskError("Anime has no destination channel")
            sources = await SourceMediaRepository(session).list_for_episode(episode_id)
            if not sources:
                raise PermanentTaskError("No source media was discovered for this episode")
            season_number = episode.season.number
            episode_number = episode.number
            anime_id = anime.id
            anime_title = anime.title
            anime_type = anime.anime_type
            release_year = anime.release_year
            channel_branding = anime.channel.branding or self.settings.default_branding
            preferred_qualities = list(anime.preferred_qualities or self.settings.preferred_qualities)
            preferred_languages = list(anime.preferred_languages or self.settings.preferred_languages)
            include_extra_audio = (
                anime.include_extra_audio
                if anime.include_extra_audio is not None
                else self.settings.include_extra_audio
            )
            poster_path = Path(anime.poster.path) if anime.poster else None

        workdir = self.settings.temp_dir / f"episode-{episode_id}-{uuid.uuid4().hex[:10]}"
        summary = ProcessSummary(episode_id=episode_id)
        try:
            if progress:
                await progress("downloading sources", 10)
            inputs = await self.processor.prepare_inputs(
                [build_source_input(media) for media in sources], workdir
            )
            if progress:
                await progress("probing media", 25)
            plans = build_plans(
                inputs,
                preferred_qualities=preferred_qualities,
                include_other_qualities=self.settings.include_other_qualities,
                preferred_languages=preferred_languages,
                include_extra_audio=include_extra_audio,
                include_unknown_audio=self.settings.include_unknown_audio,
                include_subtitles=self.settings.include_subtitles,
                allow_silent_video=self.settings.allow_silent_video,
            )
            summary.planned_qualities = [plan.quality_label for plan in plans]
            cover = poster_path if poster_path and poster_path.suffix.lower() in {".jpg", ".jpeg"} else None
            for index, plan in enumerate(plans):
                label = build_audio_label(
                    plan.audio_languages,
                    style=self.settings.audio_label_style,
                    dual_label=self.settings.dual_audio_label,
                    multi_label=self.settings.multi_audio_label,
                )
                filename = render_filename(
                    self.settings.filename_pattern,
                    anime_title=anime_title,
                    season=season_number,
                    episode=episode_number,
                    quality=plan.quality_label,
                    audio_label=label,
                    branding=channel_branding,
                    year=release_year,
                    anime_type=anime_type,
                    max_length=self.settings.max_document_filename_length,
                )
                output_path = (
                    self.settings.media_dir / f"anime-{anime_id}" / f"season-{season_number:02d}" / filename
                )
                if progress:
                    percent = 35 + int((index / max(len(plans), 1)) * 40)
                    await progress(f"remuxing {plan.quality_label}", percent)
                output_info = await self.processor.render(
                    plan,
                    output_path,
                    cover_path=cover,
                    title=f"{anime_title} - S{season_number:02d}E{episode_number:02d}",
                )
                audio_tracks = [
                    {
                        "stream_index": output_index,
                        "language": stream.language.name,
                        "language_code": stream.language.code,
                        "title": stream.title,
                        "codec": stream.codec,
                        "channels": stream.channels,
                        "bit_rate": stream.bit_rate,
                        "is_default": stream.is_default,
                    }
                    for output_index, stream in enumerate(output_info.audio)
                ]
                digest = await asyncio.to_thread(sha256_file, output_path)
                async with self.db.session() as session:
                    processed_repo = ProcessedFileRepository(session)
                    processed = await processed_repo.upsert(
                        episode_id=episode_id,
                        resolution_label=plan.quality_label,
                        width=output_info.primary_video.width if output_info.primary_video else plan.width,
                        height=output_info.primary_video.height if output_info.primary_video else plan.height,
                        filename=filename,
                        path=str(output_path),
                        size_bytes=output_path.stat().st_size,
                        sha256=digest,
                        audio_signature=plan.audio_signature,
                        subtitle_languages=output_info.subtitle_languages,
                        audio_tracks=audio_tracks,
                    )
                    summary.processed_file_ids.append(processed.id)
                    channel_id = anime.channel_id
                    if channel_id is None:
                        raise PermanentTaskError("Anime has no destination channel")
                    uploads = UploadRepository(session)
                    if await uploads.has_success(
                        channel_id=channel_id, processed_file_id=processed.id
                    ):
                        summary.skipped_uploads += 1
                        processed.status = ProcessedStatus.UPLOADED
                    else:
                        _task, created = await QueueRepository(session).enqueue(
                            kind=TaskKind.PUBLISH_EPISODE,
                            payload={
                                "processed_file_id": processed.id,
                                "episode_id": episode_id,
                                "anime_id": anime_id,
                            },
                            dedupe_key=f"publish:{processed.id}",
                            priority=publish_priority(
                                season_number,
                                episode_number,
                                plan.quality_label,
                            ),
                            max_attempts=self.settings.task_max_attempts,
                        )
                        if created:
                            summary.queued_uploads += 1
                            processed.status = ProcessedStatus.QUEUED
                    episode = await EpisodeRepository(session).get(episode_id)
                    if episode is not None:
                        episode.status = EpisodeStatus.QUEUED
            async with self.db.session() as session:
                processed_repo = ProcessedFileRepository(session)
                files = await processed_repo.list_for_episode(episode_id)
                if files and all(item.status == ProcessedStatus.UPLOADED for item in files):
                    episode = await EpisodeRepository(session).get(episode_id)
                    if episode is not None:
                        episode.status = EpisodeStatus.UPLOADED
            if progress:
                await progress("processed", 80)
            return summary
        except Exception:
            async with self.db.session() as session:
                episode = await EpisodeRepository(session).get(episode_id)
                if episode is not None and episode.status not in {
                    EpisodeStatus.UPLOADED,
                    EpisodeStatus.SKIPPED,
                }:
                    episode.status = EpisodeStatus.FAILED
            raise
        finally:
            if self.settings.cleanup_temp_files:
                shutil.rmtree(workdir, ignore_errors=True)
            else:
                logger.info("Preserving temporary directory %s", workdir)
