from __future__ import annotations

import logging
from dataclasses import dataclass, field

from anime_fortress.config import Settings
from anime_fortress.db.base import utcnow
from anime_fortress.db.enums import EpisodeStatus, SourceStatus, TaskKind
from anime_fortress.db.repositories import (
    AnimeRepository,
    AnnouncementRepository,
    EpisodeRepository,
    QueueRepository,
    SeasonRepository,
    SourceMediaRepository,
    SourceRepository,
)
from anime_fortress.db.session import Database
from anime_fortress.errors import SourceError, ValidationError
from anime_fortress.services.priorities import (
    PRIORITY_ANNOUNCEMENT,
    PRIORITY_PROCESS_EPISODE,
)
from anime_fortress.sources.base import SourceContext, SourceRuntime
from anime_fortress.sources.registry import SourceRegistry

logger = logging.getLogger(__name__)


@dataclass
class DiscoverySummary:
    anime_id: int
    episodes_found: int = 0
    episodes_queued: int = 0
    episodes_skipped: int = 0
    media_items: int = 0
    announcement_queued: bool = False
    warnings: list[str] = field(default_factory=list)


class DiscoveryService:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        registry: SourceRegistry,
        runtime: SourceRuntime,
    ) -> None:
        self.db = db
        self.settings = settings
        self.registry = registry
        self.runtime = runtime

    async def discover_for_anime(
        self, anime_id: int, *, force: bool = False
    ) -> DiscoverySummary:
        async with self.db.session() as session:
            anime = await AnimeRepository(session).get_with_relations(anime_id)
            if anime is None:
                raise ValidationError(f"Anime {anime_id} was not found")
            if anime.source is None:
                raise ValidationError("Assign a source with /addanime or /setsource first")
            if not anime.source.enabled:
                raise ValidationError(f"Source '{anime.source.name}' is disabled")
            if anime.channel is None:
                raise ValidationError("Assign a destination channel with /setchannel first")
            if self.settings.require_announcement and anime.poster is None:
                raise ValidationError("Upload a custom poster with /setposter before processing")
            adapter = self.registry.get(anime.source.adapter)
            source_id = anime.source.id
            source_name = anime.source.name
            config = dict(anime.source.config or {})
            channel_id = anime.channel.id
            result = await adapter.discover(
                config,
                SourceContext(
                    anime_title=anime.title,
                    anime_id=anime.id,
                    source_name=anime.source.name,
                ),
                self.runtime,
            )

        summary = DiscoverySummary(anime_id=anime_id, warnings=list(result.warnings))
        async with self.db.session() as session:
            anime = await AnimeRepository(session).get(anime_id)
            if anime is None:
                raise ValidationError(f"Anime {anime_id} was removed during discovery")
            source = await SourceRepository(session).get(source_id)
            if source is None:
                raise ValidationError("Source was removed during discovery")
            seasons = SeasonRepository(session)
            episodes_repo = EpisodeRepository(session)
            media_repo = SourceMediaRepository(session)
            queue = QueueRepository(session)
            announcement_repo = AnnouncementRepository(session)
            summary.episodes_found = len(result.episodes)
            for candidate in result.episodes:
                season = await seasons.get_or_create(anime_id, candidate.season)
                episode = await episodes_repo.get_or_create(
                    anime_id=anime_id,
                    season_id=season.id,
                    number=candidate.number,
                    title=candidate.title,
                )
                for media in candidate.media:
                    await media_repo.upsert(
                        episode_id=episode.id,
                        adapter=adapter.name,
                        source_item_id=media.item_id,
                        source_url=media.url,
                        local_path=media.local_path,
                        headers_env=media.headers_env,
                        quality_hint=media.quality_hint,
                        container_hint=media.container_hint,
                        size_bytes=media.size_hint,
                        audio_languages_hint=list(media.audio_languages_hint),
                    )
                    summary.media_items += 1
                already_published = episode.status == EpisodeStatus.UPLOADED
                if (already_published or episode.status == EpisodeStatus.SKIPPED) and not force:
                    summary.episodes_skipped += 1
                    continue
                if episode.status in {EpisodeStatus.FAILED, EpisodeStatus.UPLOADED}:
                    episode.status = EpisodeStatus.DISCOVERED
                    episode.skipped_reason = None
                _task, created = await queue.enqueue(
                    kind=TaskKind.PROCESS_EPISODE,
                    payload={"episode_id": episode.id, "anime_id": anime_id},
                    dedupe_key=f"process:{episode.id}",
                    priority=PRIORITY_PROCESS_EPISODE,
                    max_attempts=self.settings.task_max_attempts,
                )
                if created:
                    summary.episodes_queued += 1
            if self.settings.require_announcement:
                existing = await announcement_repo.get_success(anime_id, channel_id)
                if existing is None:
                    _task, created = await queue.enqueue(
                        kind=TaskKind.PUBLISH_ANNOUNCEMENT,
                        payload={"anime_id": anime_id, "channel_id": channel_id},
                        dedupe_key=f"announcement:{anime_id}:{channel_id}",
                        priority=PRIORITY_ANNOUNCEMENT,
                        max_attempts=self.settings.task_max_attempts,
                    )
                    summary.announcement_queued = created
            source.status = SourceStatus.VALID
            source.last_error = None
            source.last_validated_at = utcnow()
            logger.info(
                "Discovery for anime %s via %s: %s episodes, %s queued",
                anime_id,
                source_name,
                summary.episodes_found,
                summary.episodes_queued,
            )
        if not result.episodes:
            raise SourceError("The source returned no episodes")
        return summary
