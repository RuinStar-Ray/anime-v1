from __future__ import annotations

import logging
from pathlib import Path

from anime_fortress.config import Settings
from anime_fortress.db.models import Anime, Channel, Episode, Poster
from anime_fortress.db.repositories import (
    AnimeRepository,
    ChannelRepository,
    EpisodeRepository,
    PosterRepository,
)
from anime_fortress.db.session import Database
from anime_fortress.errors import MediaInspectionError, ValidationError
from anime_fortress.media.ffmpeg import FFmpeg
from anime_fortress.media.inspection import FFprobe

logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class PosterService:
    def __init__(self, db: Database, settings: Settings, probe: FFprobe, ffmpeg: FFmpeg) -> None:
        self.db = db
        self.settings = settings
        self.probe = probe
        self.ffmpeg = ffmpeg

    async def _validate_image(self, source: Path) -> tuple[int, int]:
        if not source.exists() or not source.is_file():
            raise ValidationError("Uploaded image file does not exist")
        if source.stat().st_size > MAX_IMAGE_BYTES:
            raise ValidationError("Image exceeds the 20 MB limit")
        try:
            info = await self.probe.probe(source)
        except MediaInspectionError as exc:
            raise ValidationError(f"Uploaded file is not a readable image: {exc}") from exc
        video = info.primary_video
        if video is None or not info.is_image or not video.width or not video.height:
            raise ValidationError("Uploaded file is not a supported JPEG/PNG/WebP image")
        return video.width, video.height

    async def import_poster(
        self, anime_id: int, source: Path, *, original_filename: str | None = None
    ) -> Poster:
        await self._validate_image(source)
        async with self.db.session() as session:
            anime = await AnimeRepository(session).get(anime_id)
            if anime is None:
                raise ValidationError(f"Anime {anime_id} was not found")
        target = self.settings.posters_dir / str(anime_id) / "poster.jpg"
        await self.ffmpeg.normalize_poster(source, target)
        info = await self.probe.probe(target)
        video = info.primary_video
        from anime_fortress.media.processor import sha256_file

        digest = await __import__("asyncio").to_thread(sha256_file, target)
        async with self.db.session() as session:
            repo = PosterRepository(session)
            poster = await repo.upsert(
                anime_id=anime_id,
                path=str(target),
                original_filename=original_filename,
                width=video.width if video else None,
                height=video.height if video else None,
                size_bytes=target.stat().st_size,
                sha256=digest,
            )
            return poster

    async def _import_thumbnail(self, source: Path, target: Path) -> tuple[int, int, int]:
        await self._validate_image(source)
        await self.ffmpeg.convert_image(source, target, max_dimension=1280, quality=3)
        info = await self.probe.probe(target)
        video = info.primary_video
        return (
            video.width if video else 0,
            video.height if video else 0,
            target.stat().st_size,
        )

    async def set_episode_thumbnail(
        self, episode_id: int, source: Path, *, original_filename: str | None = None
    ) -> Episode:
        target = self.settings.thumbnails_dir / "episodes" / f"{episode_id}.jpg"
        await self._import_thumbnail(source, target)
        async with self.db.session() as session:
            episode = await EpisodeRepository(session).get(episode_id)
            if episode is None:
                raise ValidationError(f"Episode {episode_id} was not found")
            episode.thumbnail_path = str(target)
            logger.info("Stored custom thumbnail for episode %s", episode_id)
            return episode

    async def set_anime_thumbnail(
        self, anime_id: int, source: Path, *, original_filename: str | None = None
    ) -> Anime:
        target = self.settings.thumbnails_dir / "anime" / f"{anime_id}.jpg"
        await self._import_thumbnail(source, target)
        async with self.db.session() as session:
            anime = await AnimeRepository(session).get(anime_id)
            if anime is None:
                raise ValidationError(f"Anime {anime_id} was not found")
            anime.default_thumbnail_path = str(target)
            return anime

    async def set_channel_thumbnail(
        self, channel_id: int, source: Path, *, original_filename: str | None = None
    ) -> Channel:
        target = self.settings.thumbnails_dir / "channels" / f"{channel_id}.jpg"
        await self._import_thumbnail(source, target)
        async with self.db.session() as session:
            channel = await ChannelRepository(session).get(channel_id)
            if channel is None:
                raise ValidationError(f"Channel {channel_id} was not found")
            channel.default_thumbnail_path = str(target)
            return channel

    async def resolve_episode_thumbnail(self, episode: Episode) -> Path | None:
        if episode.thumbnail_path and Path(episode.thumbnail_path).exists():
            return Path(episode.thumbnail_path)
        async with self.db.session() as session:
            anime = await AnimeRepository(session).get(episode.anime_id)
            if anime is None:
                return None
            if anime.default_thumbnail_path and Path(anime.default_thumbnail_path).exists():
                return Path(anime.default_thumbnail_path)
            if anime.channel_id:
                channel = await ChannelRepository(session).get(anime.channel_id)
                if channel and channel.default_thumbnail_path:
                    path = Path(channel.default_thumbnail_path)
                    if path.exists():
                        return path
        return None
