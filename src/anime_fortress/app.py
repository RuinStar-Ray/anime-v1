from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import httpx

from anime_fortress.config import Settings
from anime_fortress.db.session import Database, create_database
from anime_fortress.media.ffmpeg import FFmpeg
from anime_fortress.media.inspection import FFprobe
from anime_fortress.media.processor import MediaProcessor
from anime_fortress.metadata.registry import MetadataRegistry, default_metadata_registry
from anime_fortress.net import SafeHttpClient
from anime_fortress.services.anime import AnimeService
from anime_fortress.services.bootstrap import bootstrap_defaults
from anime_fortress.services.channels import ChannelService
from anime_fortress.services.discovery import DiscoveryService
from anime_fortress.services.media import MediaService
from anime_fortress.services.metadata import MetadataService
from anime_fortress.services.posters import PosterService
from anime_fortress.services.presentation import PresentationService
from anime_fortress.services.publish import PublishService
from anime_fortress.services.queue import QueueService
from anime_fortress.services.sources import SourceService
from anime_fortress.services.status import StatusService
from anime_fortress.services.templates import TemplateService
from anime_fortress.sources.base import SourceRuntime
from anime_fortress.sources.registry import SourceRegistry, default_source_registry
from anime_fortress.telegram.publisher import TelegramPublisher
from anime_fortress.telegram.sender import TelegramSender
from anime_fortress.templates import TemplateEngine

logger = logging.getLogger(__name__)


@dataclass
class Application:
    settings: Settings
    db: Database
    sender: TelegramSender
    metadata_http: httpx.AsyncClient
    metadata_registry: MetadataRegistry
    source_registry: SourceRegistry
    source_http: SafeHttpClient
    source_runtime: SourceRuntime
    probe: FFprobe
    ffmpeg: FFmpeg
    media_processor: MediaProcessor
    engine: TemplateEngine
    publisher: TelegramPublisher
    templates: TemplateService
    anime: AnimeService
    metadata: MetadataService
    sources: SourceService
    posters: PosterService
    presentation: PresentationService
    discovery: DiscoveryService
    media: MediaService
    publish: PublishService
    queue: QueueService
    channels: ChannelService
    status: StatusService
    _owns_metadata_http: bool = field(default=True, repr=False)
    _closed: bool = field(default=False, repr=False)

    @classmethod
    async def create(
        cls,
        settings: Settings,
        sender: TelegramSender,
        *,
        metadata_client: httpx.AsyncClient | None = None,
        source_http_client: httpx.AsyncClient | None = None,
        load_source_plugins: bool = True,
    ) -> Application:
        settings.ensure_directories()
        db = create_database(settings)
        metadata_registry, metadata_http, owns_metadata = default_metadata_registry(
            settings, metadata_client
        )
        source_http = SafeHttpClient(settings, source_http_client)
        source_registry = default_source_registry(load_plugins=load_source_plugins)
        source_runtime = SourceRuntime(
            settings=settings,
            http=source_http,
            environ=dict(os.environ),
            allowed_library_roots=settings.media_library_roots,
        )
        engine = TemplateEngine(
            missing_value=settings.missing_value_text,
            cleanup_blank_lines=settings.cleanup_blank_lines,
            max_caption_length=settings.max_caption_length,
        )
        probe = FFprobe(settings)
        ffmpeg = FFmpeg(settings)
        media_processor = MediaProcessor(settings, probe, ffmpeg, source_http)
        publisher = TelegramPublisher(sender, settings)
        templates = TemplateService(db, engine)
        anime_service = AnimeService(db)
        metadata_service = MetadataService(metadata_registry)
        source_service = SourceService(db, source_registry, source_runtime)
        poster_service = PosterService(db, settings, probe, ffmpeg)
        presentation = PresentationService(db, settings, engine)
        discovery = DiscoveryService(db, settings, source_registry, source_runtime)
        media_service = MediaService(db, settings, media_processor)
        publish_service = PublishService(
            db,
            settings,
            presentation,
            sender,
            publisher,
            poster_service,
            media_processor,
        )
        queue_service = QueueService(db)
        channel_service = ChannelService(db, sender)
        status_service = StatusService(db)
        return cls(
            settings=settings,
            db=db,
            sender=sender,
            metadata_http=metadata_http,
            metadata_registry=metadata_registry,
            source_registry=source_registry,
            source_http=source_http,
            source_runtime=source_runtime,
            probe=probe,
            ffmpeg=ffmpeg,
            media_processor=media_processor,
            engine=engine,
            publisher=publisher,
            templates=templates,
            anime=anime_service,
            metadata=metadata_service,
            sources=source_service,
            posters=poster_service,
            presentation=presentation,
            discovery=discovery,
            media=media_service,
            publish=publish_service,
            queue=queue_service,
            channels=channel_service,
            status=status_service,
            _owns_metadata_http=owns_metadata,
        )

    async def bootstrap(self) -> None:
        async with self.db.session() as session:
            await bootstrap_defaults(session, self.settings)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.source_http.close()
        if self._owns_metadata_http:
            await self.metadata_http.aclose()
        await self.db.dispose()
        logger.info("Application resources closed")
