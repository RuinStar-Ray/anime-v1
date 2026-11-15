from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from anime_fortress.config import Settings
from anime_fortress.db.enums import EpisodeStatus, ProcessedStatus, TemplateKind
from anime_fortress.db.models import (
    Anime,
    AudioTrack,
    Channel,
    Episode,
    Poster,
    ProcessedFile,
    Season,
    Template,
)
from anime_fortress.db.session import Database, create_database


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        data_dir=tmp_path / "data",
        database_url="",
        admin_user_ids=[101, 202],
        bot_token="123456:TEST-TOKEN-NOT-REAL",
        ffmpeg_path=shutil.which("ffmpeg") or "ffmpeg",
        ffprobe_path=shutil.which("ffprobe") or "ffprobe",
        preferred_qualities=["240p", "360p", "480p", "720p", "1080p"],
        max_upload_mb=48,
        retry_base_seconds=1,
        retry_max_seconds=4,
        require_announcement=False,
    )


@pytest.fixture
async def db(settings: Settings) -> AsyncIterator[Database]:
    settings.ensure_directories()
    database = create_database(settings)
    await database.create_schema()
    try:
        yield database
    finally:
        await database.dispose()


@pytest.fixture
def seed_data():
    async def _seed(
        database: Database,
        *,
        settings: Settings,
        with_poster: bool = False,
        with_media: bool = True,
        resolution: str = "720p",
        height: int = 720,
        processed_count: int = 1,
    ) -> dict[str, Any]:
        settings.ensure_directories()
        media_path = settings.media_dir / "episode.mkv"
        thumbnail_dir = settings.thumbnails_dir / "uploads"
        thumbnail_dir.mkdir(parents=True, exist_ok=True)
        if with_media:
            media_path.write_bytes(b"fake-media-bytes")
        poster_path = settings.posters_dir / "poster.jpg"
        if with_poster:
            poster_path.parent.mkdir(parents=True, exist_ok=True)
            poster_path.write_bytes(b"fake-poster-bytes")

        async with database.session() as session:
            channel = Channel(
                telegram_chat_id=-1001234567890,
                title="Anime Channel",
                username="anime_channel",
                branding="@anime_channel",
                is_verified=True,
                can_post=True,
            )
            session.add(channel)
            anime = Anime(
                title="Test Anime",
                alternative_titles=["Test Alt"],
                release_year=2024,
                anime_type="TV",
                genres=["Action"],
                rating=8.4,
                rating_source="Test",
                status="Finished",
                episode_count=12,
                synopsis="A test synopsis.",
                channel=channel,
            )
            session.add(anime)
            if with_poster:
                session.add(
                    Poster(
                        anime=anime,
                        path=str(poster_path),
                        original_filename="poster.jpg",
                        width=800,
                        height=1200,
                        size_bytes=len(b"fake-poster-bytes"),
                        sha256="0" * 64,
                    )
                )
            season = Season(anime=anime, number=1, title="Season One")
            session.add(season)
            episode = Episode(
                anime=anime,
                season=season,
                number=1,
                title="Episode One",
                status=EpisodeStatus.DISCOVERED,
            )
            session.add(episode)
            processed_files: list[ProcessedFile] = []
            processed_paths: list[Path] = []
            for index in range(processed_count):
                filename = f"S01E01-{resolution}-{index}.mkv"
                if with_media:
                    path = settings.media_dir / filename
                    path.write_bytes(b"fake-media-" + bytes([index]))
                else:
                    path = media_path
                processed_paths.append(path)
                processed = ProcessedFile(
                    episode=episode,
                    resolution_label=resolution,
                    width=(height * 16) // 9,
                    height=height,
                    filename=filename,
                    path=str(path),
                    size_bytes=16,
                    sha256=f"{index:064x}",
                    audio_signature=f"signature-{index}",
                    subtitle_languages=[],
                    status=ProcessedStatus.READY,
                    audio_tracks=[
                        AudioTrack(
                            stream_index=1,
                            language="Hindi",
                            language_code="hin",
                            title="Hindi",
                            codec="aac",
                            channels=2,
                            is_default=True,
                        ),
                        AudioTrack(
                            stream_index=2,
                            language="English",
                            language_code="eng",
                            title="English",
                            codec="aac",
                            channels=2,
                            is_default=False,
                        ),
                    ],
                )
                session.add(processed)
                processed_files.append(processed)
            await session.flush()
            return {
                "channel_id": channel.id,
                "anime_id": anime.id,
                "season_id": season.id,
                "episode_id": episode.id,
                "processed_ids": [item.id for item in processed_files],
                "processed_paths": processed_paths,
                "poster_path": poster_path if with_poster else None,
                "thumbnail_path": thumbnail_dir
                / f"anime-{anime.id}-s01e01-{resolution}.jpg",
                "media_path": media_path,
            }

    return _seed


@pytest.fixture
def template_factory(db: Database):
    async def _create(name: str, body: str, *, kind: TemplateKind = TemplateKind.CAPTION):
        async with db.session() as session:
            template = Template(name=name, kind=kind, body=body, is_system=False)
            session.add(template)
            await session.flush()
            return template.id

    return _create
