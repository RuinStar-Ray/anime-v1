from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from anime_fortress.db.enums import (
    ACTIVE_QUEUE_STATUSES,
    EpisodeStatus,
    ProcessedStatus,
    QueueStatus,
    TaskKind,
    TemplateKind,
    UploadStatus,
)
from anime_fortress.db.models import (
    Anime,
    AnnouncementRecord,
    AudioTrack,
    AuditLog,
    Channel,
    Episode,
    Poster,
    ProcessedFile,
    QueueTask,
    Season,
    Setting,
    Source,
    SourceMedia,
    Template,
    UploadRecord,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class TemplateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, template_id: int) -> Template | None:
        return await self.session.get(Template, template_id)

    async def get_by_name(self, name: str) -> Template | None:
        result = await self.session.execute(select(Template).where(Template.name == name))
        return result.scalar_one_or_none()

    async def list(self, kind: TemplateKind | None = None) -> list[Template]:
        query = select(Template).order_by(Template.name)
        if kind:
            query = query.where(Template.kind == kind)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def create(
        self, *, name: str, kind: TemplateKind, body: str, description: str | None = None,
        is_system: bool = False,
    ) -> Template:
        template = Template(
            name=name, kind=kind, body=body, description=description, is_system=is_system
        )
        self.session.add(template)
        await self.session.flush()
        return template

    async def update(self, template: Template, *, body: str | None = None, name: str | None = None) -> Template:
        if body is not None:
            template.body = body
        if name is not None:
            template.name = name
        await self.session.flush()
        return template


class SourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, source_id: int) -> Source | None:
        return await self.session.get(Source, source_id)

    async def get_by_name(self, name: str) -> Source | None:
        result = await self.session.execute(select(Source).where(Source.name == name))
        return result.scalar_one_or_none()

    async def list(self) -> list[Source]:
        result = await self.session.execute(select(Source).order_by(Source.name))
        return list(result.scalars().all())

    async def create(self, *, name: str, adapter: str, config: dict[str, Any]) -> Source:
        source = Source(name=name, adapter=adapter, config=config)
        self.session.add(source)
        await self.session.flush()
        return source


class ChannelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, channel_id: int) -> Channel | None:
        return await self.session.get(Channel, channel_id)

    async def get_by_chat_id(self, chat_id: int) -> Channel | None:
        result = await self.session.execute(
            select(Channel).where(Channel.telegram_chat_id == chat_id)
        )
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> Channel | None:
        normalized = username.lstrip("@").lower()
        result = await self.session.execute(
            select(Channel).where(func.lower(Channel.username) == normalized)
        )
        return result.scalar_one_or_none()

    async def list(self) -> list[Channel]:
        result = await self.session.execute(select(Channel).order_by(Channel.title))
        return list(result.scalars().all())

    async def create_or_update(
        self,
        *,
        telegram_chat_id: int,
        title: str,
        username: str | None,
        branding: str | None = None,
    ) -> Channel:
        channel = await self.get_by_chat_id(telegram_chat_id)
        if channel is None:
            channel = Channel(
                telegram_chat_id=telegram_chat_id,
                title=title,
                username=username,
                branding=branding or "",
            )
            self.session.add(channel)
        else:
            channel.title = title
            channel.username = username
            if branding is not None:
                channel.branding = branding
        await self.session.flush()
        return channel


class AnimeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, anime_id: int) -> Anime | None:
        return await self.session.get(Anime, anime_id)

    async def get_full(self, anime_id: int) -> Anime | None:
        result = await self.session.execute(
            select(Anime)
            .where(Anime.id == anime_id)
            .options(
                selectinload(Anime.source),
                selectinload(Anime.channel),
                selectinload(Anime.poster),
                selectinload(Anime.announcement_template),
                selectinload(Anime.seasons).selectinload(Season.episodes),
            )
        )
        return result.scalar_one_or_none()

    async def get_with_relations(self, anime_id: int) -> Anime | None:
        result = await self.session.execute(
            select(Anime)
            .where(Anime.id == anime_id)
            .options(
                selectinload(Anime.source),
                selectinload(Anime.channel),
                selectinload(Anime.poster),
                selectinload(Anime.announcement_template),
            )
        )
        return result.scalar_one_or_none()

    async def list(self) -> list[Anime]:
        result = await self.session.execute(select(Anime).order_by(Anime.title))
        return list(result.scalars().all())

    async def create(self, **values: Any) -> Anime:
        anime = Anime(**values)
        self.session.add(anime)
        await self.session.flush()
        return anime

    async def update_metadata(
        self,
        anime: Anime,
        *,
        fields: dict[str, Any],
        provider: str | None = None,
        external_id: str | None = None,
        provenance: dict[str, Any] | None = None,
        fetched_at: datetime | None = None,
    ) -> Anime:
        for key, value in fields.items():
            if hasattr(anime, key):
                setattr(anime, key, value)
        if provider is not None:
            anime.metadata_provider = provider
        if external_id is not None:
            anime.metadata_external_id = external_id
        if fetched_at is not None:
            anime.metadata_fetched_at = fetched_at
        if provenance is not None:
            merged = dict(anime.metadata_provenance or {})
            merged.update(provenance)
            anime.metadata_provenance = merged
        await self.session.flush()
        return anime

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(Anime.id)))
        return int(result.scalar_one())


class SeasonRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(
        self, anime_id: int, number: int, title: str | None = None
    ) -> Season:
        result = await self.session.execute(
            select(Season).where(Season.anime_id == anime_id, Season.number == number)
        )
        season = result.scalar_one_or_none()
        if season is None:
            season = Season(anime_id=anime_id, number=number, title=title)
            self.session.add(season)
            await self.session.flush()
        return season

    async def list_for_anime(self, anime_id: int) -> list[Season]:
        result = await self.session.execute(
            select(Season).where(Season.anime_id == anime_id).order_by(Season.number)
        )
        return list(result.scalars().all())


class EpisodeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, episode_id: int) -> Episode | None:
        return await self.session.get(Episode, episode_id)

    async def get_full(self, episode_id: int) -> Episode | None:
        result = await self.session.execute(
            select(Episode)
            .where(Episode.id == episode_id)
            .options(selectinload(Episode.anime), selectinload(Episode.season))
        )
        return result.scalar_one_or_none()

    async def get_with_files(self, episode_id: int) -> Episode | None:
        result = await self.session.execute(
            select(Episode)
            .where(Episode.id == episode_id)
            .options(
                selectinload(Episode.processed_files).selectinload(ProcessedFile.audio_tracks),
                selectinload(Episode.anime),
                selectinload(Episode.season),
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create(
        self, *, anime_id: int, season_id: int, number: int, title: str | None = None
    ) -> Episode:
        result = await self.session.execute(
            select(Episode).where(Episode.season_id == season_id, Episode.number == number)
        )
        episode = result.scalar_one_or_none()
        if episode is None:
            episode = Episode(
                anime_id=anime_id, season_id=season_id, number=number, title=title
            )
            self.session.add(episode)
            await self.session.flush()
        elif title and not episode.title:
            episode.title = title
        return episode

    async def list_for_anime(self, anime_id: int) -> list[Episode]:
        result = await self.session.execute(
            select(Episode)
            .where(Episode.anime_id == anime_id)
            .order_by(Episode.season_id, Episode.number)
        )
        return list(result.scalars().all())

    async def list_for_processing(self, anime_id: int) -> list[Episode]:
        result = await self.session.execute(
            select(Episode)
            .where(
                Episode.anime_id == anime_id,
                Episode.status.not_in([EpisodeStatus.UPLOADED, EpisodeStatus.SKIPPED]),
            )
            .order_by(Episode.season_id, Episode.number)
            .options(selectinload(Episode.season))
        )
        return list(result.scalars().all())

    async def has_prior_unuploaded(self, episode: Episode) -> Episode | None:
        current_season = await self.session.get(Season, episode.season_id)
        if current_season is None:
            return None
        has_files = exists(
            select(ProcessedFile.id).where(ProcessedFile.episode_id == Episode.id)
        )
        result = await self.session.execute(
            select(Episode)
            .join(Season, Season.id == Episode.season_id)
            .where(
                Episode.anime_id == episode.anime_id,
                Episode.id != episode.id,
                Episode.status.not_in([EpisodeStatus.UPLOADED, EpisodeStatus.SKIPPED]),
                has_files,
                or_(
                    Season.number < current_season.number,
                    and_(
                        Season.number == current_season.number,
                        Episode.number < episode.number,
                    ),
                ),
            )
            .order_by(Season.number, Episode.number)
            .options(selectinload(Episode.season))
        )
        return result.scalars().first()

    async def set_status(self, episode: Episode, status: EpisodeStatus, *, error: str | None = None) -> None:
        episode.status = status
        if error is not None:
            episode.skipped_reason = error[:255]
        await self.session.flush()


class SourceMediaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self,
        *,
        episode_id: int,
        adapter: str,
        source_item_id: str,
        source_url: str | None,
        local_path: str | None,
        headers_env: str | None,
        quality_hint: str | None,
        container_hint: str | None,
        size_bytes: int | None,
        audio_languages_hint: list[str],
    ) -> SourceMedia:
        result = await self.session.execute(
            select(SourceMedia).where(
                SourceMedia.episode_id == episode_id,
                SourceMedia.adapter == adapter,
                SourceMedia.source_item_id == source_item_id,
            )
        )
        media = result.scalar_one_or_none()
        if media is None:
            media = SourceMedia(
                episode_id=episode_id,
                adapter=adapter,
                source_item_id=source_item_id,
                source_url=source_url,
                local_path=local_path,
                headers_env=headers_env,
                quality_hint=quality_hint,
                container_hint=container_hint,
                size_bytes=size_bytes,
                audio_languages_hint=audio_languages_hint,
            )
            self.session.add(media)
        else:
            media.source_url = source_url
            media.local_path = local_path
            media.headers_env = headers_env
            media.quality_hint = quality_hint
            media.container_hint = container_hint
            media.size_bytes = size_bytes
            media.audio_languages_hint = audio_languages_hint
        await self.session.flush()
        return media

    async def list_for_episode(self, episode_id: int) -> list[SourceMedia]:
        result = await self.session.execute(
            select(SourceMedia)
            .where(SourceMedia.episode_id == episode_id)
            .order_by(SourceMedia.id)
        )
        return list(result.scalars().all())


class PosterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_anime(self, anime_id: int) -> Poster | None:
        result = await self.session.execute(select(Poster).where(Poster.anime_id == anime_id))
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        anime_id: int,
        path: str,
        original_filename: str | None,
        width: int | None,
        height: int | None,
        size_bytes: int | None,
        sha256: str | None,
    ) -> Poster:
        poster = await self.get_for_anime(anime_id)
        if poster is None:
            poster = Poster(
                anime_id=anime_id,
                path=path,
                original_filename=original_filename,
                width=width,
                height=height,
                size_bytes=size_bytes,
                sha256=sha256,
            )
            self.session.add(poster)
        else:
            poster.path = path
            poster.original_filename = original_filename
            poster.width = width
            poster.height = height
            poster.size_bytes = size_bytes
            poster.sha256 = sha256
        await self.session.flush()
        return poster


class ProcessedFileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, file_id: int) -> ProcessedFile | None:
        return await self.session.get(ProcessedFile, file_id)

    async def get_full(self, file_id: int) -> ProcessedFile | None:
        result = await self.session.execute(
            select(ProcessedFile)
            .where(ProcessedFile.id == file_id)
            .options(
                selectinload(ProcessedFile.audio_tracks),
                selectinload(ProcessedFile.episode).selectinload(Episode.season),
                selectinload(ProcessedFile.episode).selectinload(Episode.anime),
            )
        )
        return result.scalar_one_or_none()

    async def list_for_episode(self, episode_id: int) -> list[ProcessedFile]:
        result = await self.session.execute(
            select(ProcessedFile)
            .where(ProcessedFile.episode_id == episode_id)
            .order_by(ProcessedFile.height, ProcessedFile.id)
            .options(selectinload(ProcessedFile.audio_tracks))
        )
        return list(result.scalars().all())

    async def upsert(
        self,
        *,
        episode_id: int,
        resolution_label: str,
        width: int,
        height: int,
        filename: str,
        path: str,
        size_bytes: int,
        sha256: str,
        audio_signature: str,
        subtitle_languages: list[str],
        audio_tracks: list[dict[str, Any]],
    ) -> ProcessedFile:
        result = await self.session.execute(
            select(ProcessedFile)
            .where(
                ProcessedFile.episode_id == episode_id,
                ProcessedFile.resolution_label == resolution_label,
                ProcessedFile.audio_signature == audio_signature,
            )
            .options(selectinload(ProcessedFile.audio_tracks))
        )
        processed = result.scalar_one_or_none()
        if processed is None:
            processed = ProcessedFile(
                episode_id=episode_id,
                resolution_label=resolution_label,
                width=width,
                height=height,
                filename=filename,
                path=path,
                size_bytes=size_bytes,
                sha256=sha256,
                audio_signature=audio_signature,
                subtitle_languages=subtitle_languages,
                status=ProcessedStatus.READY,
            )
            self.session.add(processed)
            await self.session.flush()
        else:
            processed.width = width
            processed.height = height
            processed.filename = filename
            processed.path = path
            processed.size_bytes = size_bytes
            processed.sha256 = sha256
            processed.subtitle_languages = subtitle_languages
            processed.status = ProcessedStatus.READY
            processed.last_error = None
            for track in list(processed.audio_tracks):
                await self.session.delete(track)
            await self.session.flush()
        for track in audio_tracks:
            processed.audio_tracks.append(
                AudioTrack(
                    stream_index=int(track["stream_index"]),
                    language=str(track["language"]),
                    language_code=track.get("language_code"),
                    title=track.get("title"),
                    codec=track.get("codec"),
                    channels=track.get("channels"),
                    bit_rate=track.get("bit_rate"),
                    is_default=bool(track.get("is_default", False)),
                )
            )
        await self.session.flush()
        return processed

    async def set_status(self, processed: ProcessedFile, status: ProcessedStatus) -> None:
        processed.status = status
        await self.session.flush()

    async def count_by_status(self) -> dict[str, int]:
        result = await self.session.execute(
            select(ProcessedFile.status, func.count(ProcessedFile.id)).group_by(ProcessedFile.status)
        )
        return {str(row[0].value if hasattr(row[0], "value") else row[0]): int(row[1]) for row in result}


class QueueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(
        self,
        *,
        kind: TaskKind,
        payload: dict[str, Any],
        dedupe_key: str | None = None,
        priority: int = 100,
        delay_seconds: int = 0,
        max_attempts: int = 5,
    ) -> tuple[QueueTask, bool]:
        if dedupe_key:
            result = await self.session.execute(
                select(QueueTask).where(
                    QueueTask.dedupe_key == dedupe_key,
                    QueueTask.status.in_(ACTIVE_QUEUE_STATUSES),
                )
            )
            existing = result.scalars().first()
            if existing:
                return existing, False
        now = utcnow()
        task = QueueTask(
            kind=kind,
            payload=payload,
            dedupe_key=dedupe_key,
            priority=priority,
            status=QueueStatus.QUEUED,
            attempts=0,
            max_attempts=max_attempts,
            available_at=now + timedelta(seconds=delay_seconds),
        )
        self.session.add(task)
        await self.session.flush()
        return task, True

    async def claim_next(self, worker_id: str) -> QueueTask | None:
        now = utcnow()
        result = await self.session.execute(
            select(QueueTask.id)
            .where(QueueTask.status == QueueStatus.QUEUED, QueueTask.available_at <= now)
            .order_by(QueueTask.priority, QueueTask.available_at, QueueTask.id)
            .limit(1)
        )
        task_id = result.scalar_one_or_none()
        if task_id is None:
            return None
        claimed = await self.session.execute(
            update(QueueTask)
            .where(QueueTask.id == task_id, QueueTask.status == QueueStatus.QUEUED)
            .values(
                status=QueueStatus.RUNNING,
                locked_at=now,
                locked_by=worker_id,
                attempts=QueueTask.attempts + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            await self.session.rollback()
            return None
        task = await self.session.get(QueueTask, task_id)
        await self.session.commit()
        if task:
            await self.session.refresh(task)
        return task

    async def complete(self, task_id: int, result: dict[str, Any] | None = None) -> None:
        await self.session.execute(
            update(QueueTask)
            .where(QueueTask.id == task_id)
            .values(
                status=QueueStatus.SUCCEEDED,
                result=result,
                locked_at=None,
                locked_by=None,
                finished_at=utcnow(),
                last_error=None,
                progress=100,
                stage="done",
            )
        )
        await self.session.commit()

    async def set_progress(self, task_id: int, *, stage: str, progress: int) -> None:
        await self.session.execute(
            update(QueueTask)
            .where(QueueTask.id == task_id, QueueTask.status == QueueStatus.RUNNING)
            .values(stage=stage[:80], progress=max(0, min(100, progress)))
        )
        await self.session.commit()

    async def fail(
        self,
        task_id: int,
        error: str,
        *,
        retryable: bool,
        retry_after_seconds: int | None = None,
        base_retry: int = 30,
        max_retry: int = 3600,
    ) -> QueueStatus:
        task = await self.session.get(QueueTask, task_id)
        if task is None:
            return QueueStatus.FAILED
        status = QueueStatus.FAILED
        available_at = task.available_at
        if retryable and task.attempts < task.max_attempts:
            backoff = retry_after_seconds or min(base_retry * (2 ** max(task.attempts - 1, 0)), max_retry)
            status = QueueStatus.QUEUED
            available_at = utcnow() + timedelta(seconds=backoff)
        elif retryable and task.attempts >= task.max_attempts:
            status = QueueStatus.DEAD
        task.status = status
        task.last_error = error[:4000]
        task.available_at = available_at
        task.locked_at = None
        task.locked_by = None
        task.stage = "retry scheduled" if status == QueueStatus.QUEUED else "failed"
        if status != QueueStatus.QUEUED:
            task.finished_at = utcnow()
        await self.session.commit()
        return status

    async def release_running(self) -> int:
        result = await self.session.execute(
            select(QueueTask).where(QueueTask.status == QueueStatus.RUNNING)
        )
        tasks = list(result.scalars().all())
        for task in tasks:
            task.status = QueueStatus.QUEUED
            task.attempts = max(task.attempts - 1, 0)
            task.available_at = utcnow()
            task.locked_at = None
            task.locked_by = None
            task.finished_at = None
            task.stage = "requeued"
            task.last_error = (task.last_error or "") + " [requeued after worker restart]"
        await self.session.commit()
        return len(tasks)

    async def reset_stale(self, stale_seconds: int, *, max_attempts: int = 5) -> int:
        cutoff = utcnow() - timedelta(seconds=stale_seconds)
        result = await self.session.execute(
            select(QueueTask).where(
                QueueTask.status == QueueStatus.RUNNING,
                QueueTask.locked_at < cutoff,
            )
        )
        tasks = list(result.scalars().all())
        for task in tasks:
            limit = min(task.max_attempts, max_attempts)
            if task.attempts >= limit:
                task.status = QueueStatus.DEAD
                task.finished_at = utcnow()
                task.last_error = (task.last_error or "") + " [stale task exceeded attempts]"
            else:
                task.status = QueueStatus.QUEUED
                task.available_at = utcnow()
                task.last_error = (task.last_error or "") + " [recovered after stale lock]"
            task.locked_at = None
            task.locked_by = None
            task.stage = "recovered"
        await self.session.commit()
        return len(tasks)

    async def retry(self, task_id: int) -> QueueTask | None:
        task = await self.session.get(QueueTask, task_id)
        if task is None:
            return None
        if task.status not in {QueueStatus.FAILED, QueueStatus.DEAD, QueueStatus.CANCELLED}:
            return None
        task.status = QueueStatus.QUEUED
        task.attempts = 0
        task.available_at = utcnow()
        task.locked_at = None
        task.locked_by = None
        task.finished_at = None
        task.last_error = None
        await self.session.commit()
        return task

    async def retry_failed(self) -> int:
        result = await self.session.execute(
            select(QueueTask).where(
                QueueTask.status.in_([QueueStatus.FAILED, QueueStatus.DEAD, QueueStatus.CANCELLED])
            )
        )
        tasks = list(result.scalars().all())
        for task in tasks:
            task.status = QueueStatus.QUEUED
            task.attempts = 0
            task.available_at = utcnow()
            task.locked_at = None
            task.locked_by = None
            task.finished_at = None
            task.last_error = None
        await self.session.commit()
        return len(tasks)

    async def cancel(self, task_id: int) -> QueueTask | None:
        task = await self.session.get(QueueTask, task_id)
        if task is None or task.status not in ACTIVE_QUEUE_STATUSES:
            return None
        task.status = QueueStatus.CANCELLED
        task.finished_at = utcnow()
        await self.session.commit()
        return task

    async def get(self, task_id: int) -> QueueTask | None:
        return await self.session.get(QueueTask, task_id)

    async def counts(self) -> dict[str, int]:
        result = await self.session.execute(
            select(QueueTask.status, func.count(QueueTask.id)).group_by(QueueTask.status)
        )
        return {str(row[0].value if hasattr(row[0], "value") else row[0]): int(row[1]) for row in result}

    async def recent(self, limit: int = 20) -> list[QueueTask]:
        result = await self.session.execute(
            select(QueueTask).order_by(QueueTask.id.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def active_count(self) -> int:
        result = await self.session.execute(
            select(func.count(QueueTask.id)).where(QueueTask.status.in_(ACTIVE_QUEUE_STATUSES))
        )
        return int(result.scalar_one())


class UploadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def has_success(self, *, channel_id: int, processed_file_id: int) -> bool:
        result = await self.session.execute(
            select(func.count(UploadRecord.id)).where(
                UploadRecord.channel_id == channel_id,
                UploadRecord.processed_file_id == processed_file_id,
                UploadRecord.status == UploadStatus.SUCCESS,
            )
        )
        return int(result.scalar_one()) > 0

    async def has_success_checksum(self, *, channel_id: int, sha256: str) -> bool:
        result = await self.session.execute(
            select(func.count(UploadRecord.id)).where(
                UploadRecord.channel_id == channel_id,
                UploadRecord.file_sha256 == sha256,
                UploadRecord.status == UploadStatus.SUCCESS,
            )
        )
        return int(result.scalar_one()) > 0

    async def record(
        self,
        *,
        anime_id: int,
        episode_id: int | None,
        processed_file_id: int | None,
        channel_id: int,
        resolution_label: str | None,
        file_name: str,
        file_sha256: str | None,
        file_size: int | None,
        caption: str | None,
        telegram_message_id: int | None,
        telegram_file_id: str | None,
        status: UploadStatus,
        error: str | None = None,
    ) -> UploadRecord:
        record = UploadRecord(
            anime_id=anime_id,
            episode_id=episode_id,
            processed_file_id=processed_file_id,
            channel_id=channel_id,
            resolution_label=resolution_label,
            file_name=file_name,
            file_sha256=file_sha256,
            file_size=file_size,
            caption=caption,
            telegram_message_id=telegram_message_id,
            telegram_file_id=telegram_file_id,
            status=status,
            error=error,
            created_at=utcnow(),
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def history(self, limit: int = 20, anime_id: int | None = None) -> list[UploadRecord]:
        query = select(UploadRecord).order_by(UploadRecord.id.desc()).limit(limit)
        if anime_id is not None:
            query = query.where(UploadRecord.anime_id == anime_id)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(UploadRecord.id)))
        return int(result.scalar_one())


class AnnouncementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_success(self, anime_id: int, channel_id: int) -> AnnouncementRecord | None:
        result = await self.session.execute(
            select(AnnouncementRecord).where(
                AnnouncementRecord.anime_id == anime_id,
                AnnouncementRecord.channel_id == channel_id,
                AnnouncementRecord.status == UploadStatus.SUCCESS,
            )
        )
        return result.scalars().first()

    async def record(
        self,
        *,
        anime_id: int,
        channel_id: int,
        template_id: int | None,
        message_id: int | None,
        detail_message_id: int | None,
        text: str,
        poster_path: str | None,
        telegram_file_id: str | None,
        status: UploadStatus,
        error: str | None = None,
    ) -> AnnouncementRecord:
        record = AnnouncementRecord(
            anime_id=anime_id,
            channel_id=channel_id,
            template_id=template_id,
            message_id=message_id,
            detail_message_id=detail_message_id,
            text=text,
            poster_path=poster_path,
            telegram_file_id=telegram_file_id,
            status=status,
            error=error,
            published_at=utcnow() if status == UploadStatus.SUCCESS else None,
            created_at=utcnow(),
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def latest(self, limit: int = 20) -> list[AnnouncementRecord]:
        result = await self.session.execute(
            select(AnnouncementRecord).order_by(AnnouncementRecord.id.desc()).limit(limit)
        )
        return list(result.scalars().all())


class SettingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, key: str, default: Any = None) -> Any:
        setting = await self.session.get(Setting, key)
        return setting.value if setting else default

    async def set(self, key: str, value: Any) -> None:
        setting = await self.session.get(Setting, key)
        if setting is None:
            setting = Setting(key=key, value=value, updated_at=utcnow())
            self.session.add(setting)
        else:
            setting.value = value
            setting.updated_at = utcnow()
        await self.session.flush()

    async def is_worker_paused(self) -> bool:
        return bool(await self.get("worker_paused", False))


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def log(self, *, admin_id: int | None, action: str, detail: dict[str, Any]) -> None:
        self.session.add(
            AuditLog(admin_id=admin_id, action=action, detail=detail, created_at=utcnow())
        )
        await self.session.flush()
