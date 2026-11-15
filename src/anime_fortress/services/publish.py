from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from anime_fortress.config import Settings
from anime_fortress.db.base import utcnow
from anime_fortress.db.enums import EpisodeStatus, ProcessedStatus, UploadStatus
from anime_fortress.db.repositories import (
    AnimeRepository,
    AnnouncementRepository,
    EpisodeRepository,
    ProcessedFileRepository,
    UploadRepository,
)
from anime_fortress.db.session import Database
from anime_fortress.errors import (
    PermanentTaskError,
    RetryableTaskError,
    TelegramPublishError,
    UploadTooLarge,
)
from anime_fortress.media.processor import MediaProcessor
from anime_fortress.services.posters import PosterService
from anime_fortress.services.presentation import PresentationService
from anime_fortress.telegram.publisher import TelegramPublisher
from anime_fortress.telegram.sender import TelegramSender

logger = logging.getLogger(__name__)


class PublishService:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        presentation: PresentationService,
        sender: TelegramSender,
        publisher: TelegramPublisher,
        posters: PosterService,
        processor: MediaProcessor,
    ) -> None:
        self.db = db
        self.settings = settings
        self.presentation = presentation
        self.sender = sender
        self.publisher = publisher
        self.posters = posters
        self.processor = processor

    async def publish_announcement(self, anime_id: int) -> int:
        async with self.db.session() as session:
            announcements = AnnouncementRepository(session)
            anime = await AnimeRepository(session).get_full(anime_id)
            if anime is None:
                raise PermanentTaskError(f"Anime {anime_id} was not found")
            if anime.channel is None:
                raise PermanentTaskError("Anime has no destination channel")
            existing = await announcements.get_success(anime_id, anime.channel.id)
            if existing:
                return existing.message_id or 0
            channel_id = anime.channel.id
            channel_title = anime.channel.title
            title = anime.title
        member = await self.sender.get_bot_member(channel_id)
        if not member.can_post:
            raise PermanentTaskError(
                f"The bot cannot post to '{channel_title}' (status={member.status}). "
                "Add the bot as a channel administrator with post permissions."
            )
        rendered = await self.presentation.render_announcement(anime_id)
        try:
            delivery = await self.publisher.deliver_announcement(
                chat_id=channel_id,
                title=title,
                text=rendered.text,
                poster_path=rendered.poster_path,
            )
        except (TelegramPublishError, RetryableTaskError) as exc:
            async with self.db.session() as session:
                await AnnouncementRepository(session).record(
                    anime_id=anime_id,
                    channel_id=channel_id,
                    template_id=rendered.template_id,
                    message_id=None,
                    detail_message_id=None,
                    text=rendered.text,
                    poster_path=str(rendered.poster_path) if rendered.poster_path else None,
                    telegram_file_id=None,
                    status=UploadStatus.FAILED,
                    error=str(exc)[:4000],
                )
            raise
        async with self.db.session() as session:
            repo = AnnouncementRepository(session)
            existing = await repo.get_success(anime_id, channel_id)
            if existing:
                return existing.message_id or 0
            try:
                record = await repo.record(
                    anime_id=anime_id,
                    channel_id=channel_id,
                    template_id=rendered.template_id,
                    message_id=delivery.message_id,
                    detail_message_id=delivery.detail_message_id,
                    text=rendered.text,
                    poster_path=str(rendered.poster_path) if rendered.poster_path else None,
                    telegram_file_id=delivery.file_id,
                    status=UploadStatus.SUCCESS,
                )
            except IntegrityError:
                await session.rollback()
                existing = await repo.get_success(anime_id, channel_id)
                if existing:
                    return existing.message_id or 0
                raise
            logger.info("Published announcement %s for anime %s", record.message_id, anime_id)
            return delivery.message_id

    async def publish_episode(self, processed_file_id: int) -> int | None:
        async with self.db.session() as session:
            processed = await ProcessedFileRepository(session).get_full(processed_file_id)
            if processed is None:
                raise PermanentTaskError(f"Processed file {processed_file_id} was not found")
            episode = processed.episode
            if episode.status == EpisodeStatus.SKIPPED:
                raise PermanentTaskError("Episode was skipped by an administrator")
            anime = await AnimeRepository(session).get_full(episode.anime_id)
            if anime is None:
                raise PermanentTaskError("Anime record was not found")
            if anime.channel is None:
                raise PermanentTaskError("Anime has no destination channel")
            channel = anime.channel
            uploads = UploadRepository(session)
            if await uploads.has_success(
                channel_id=channel.id, processed_file_id=processed.id
            ):
                return None
            announcements = AnnouncementRepository(session)
            if self.settings.require_announcement:
                announcement = await announcements.get_success(anime.id, channel.id)
                if announcement is None:
                    raise RetryableTaskError(
                        "Announcement is not published yet; episode upload will wait",
                        retry_after_seconds=60,
                    )
            if self.settings.strict_episode_order:
                prior = await EpisodeRepository(session).has_prior_unuploaded(episode)
                if prior is not None:
                    raise RetryableTaskError(
                        f"Waiting for earlier episode S{prior.season.number:02d}E"
                        f"{prior.number:02d} to finish uploading",
                        retry_after_seconds=120,
                    )
                siblings = await ProcessedFileRepository(session).list_for_episode(episode.id)
                lower = [
                    item
                    for item in siblings
                    if item.height < processed.height and item.status != ProcessedStatus.UPLOADED
                ]
                if lower:
                    labels = ", ".join(item.resolution_label for item in lower)
                    raise RetryableTaskError(
                        f"Waiting for lower-quality version(s) to upload first: {labels}",
                        retry_after_seconds=90,
                    )
            if await uploads.has_success_checksum(channel_id=channel.id, sha256=processed.sha256):
                raise PermanentTaskError(
                    "An identical file (matching SHA-256) was already uploaded to this channel"
                )
            channel_id = channel.id
            channel_title = channel.title
            anime_id = anime.id
            episode_id = episode.id
            season_number = episode.season.number
            episode_number = episode.number
            filename = processed.filename
            media_path = Path(processed.path)
            thumbnail_custom = await self.posters.resolve_episode_thumbnail(episode)
        member = await self.sender.get_bot_member(channel_id)
        if not member.can_post:
            raise PermanentTaskError(
                f"The bot cannot post to '{channel_title}' (status={member.status}). "
                "Add the bot as a channel administrator with post permissions."
            )
        if not media_path.exists():
            raise PermanentTaskError(f"Processed media file is missing: {media_path.name}")
        try:
            self.processor.ensure_uploadable(media_path)
        except UploadTooLarge as exc:
            raise PermanentTaskError(str(exc)) from exc
        thumbnail_path = (
            self.settings.thumbnails_dir
            / "uploads"
            / f"anime-{anime_id}-s{season_number:02d}e{episode_number:02d}-{processed.resolution_label}.jpg"
        )
        regenerate = (
            not thumbnail_path.exists()
            or (thumbnail_custom is not None and thumbnail_custom.stat().st_mtime > thumbnail_path.stat().st_mtime)
        )
        if regenerate:
            await self.processor.make_telegram_thumbnail(
                media_path, thumbnail_path, custom_image=thumbnail_custom
            )
        rendered = await self.presentation.render_caption(processed_file_id)
        try:
            sent = await self.publisher.deliver_episode(
                chat_id=channel_id,
                path=media_path,
                filename=filename,
                caption=rendered.text,
                thumbnail_path=thumbnail_path,
            )
        except (TelegramPublishError, RetryableTaskError) as exc:
            async with self.db.session() as session:
                await UploadRepository(session).record(
                    anime_id=anime_id,
                    episode_id=episode_id,
                    processed_file_id=processed_file_id,
                    channel_id=channel_id,
                    resolution_label=processed.resolution_label,
                    file_name=filename,
                    file_size=media_path.stat().st_size,
                    file_sha256=processed.sha256,
                    caption=rendered.text,
                    telegram_message_id=None,
                    telegram_file_id=None,
                    status=UploadStatus.FAILED,
                    error=str(exc)[:4000],
                )
            raise
        async with self.db.session() as session:
            processed_repo = ProcessedFileRepository(session)
            processed_current = await processed_repo.get(processed_file_id)
            if processed_current is None:
                raise PermanentTaskError("Processed file disappeared after upload")
            try:
                await UploadRepository(session).record(
                    anime_id=anime_id,
                    episode_id=episode_id,
                    processed_file_id=processed_file_id,
                    channel_id=channel_id,
                    resolution_label=processed_current.resolution_label,
                    file_name=filename,
                    file_size=media_path.stat().st_size,
                    file_sha256=processed_current.sha256,
                    caption=rendered.text,
                    telegram_message_id=sent.message_id,
                    telegram_file_id=sent.file_id,
                    status=UploadStatus.SUCCESS,
                )
            except IntegrityError:
                logger.warning("Upload record already exists for processed file %s", processed_file_id)
            processed_current.status = ProcessedStatus.UPLOADED
            processed_current.telegram_file_id = sent.file_id
            processed_current.last_error = None
            episode = await EpisodeRepository(session).get(episode_id)
            if episode is not None:
                siblings = await processed_repo.list_for_episode(episode_id)
                all_uploaded = siblings and all(
                    item.status == ProcessedStatus.UPLOADED or item.id == processed_file_id
                    for item in siblings
                )
                if all_uploaded:
                    episode.status = EpisodeStatus.UPLOADED
                    episode.published_at = utcnow()
                else:
                    episode.status = EpisodeStatus.QUEUED
            logger.info(
                "Uploaded %s to channel %s (message %s)", filename, channel_id, sent.message_id
            )
            return sent.message_id
