from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from anime_fortress.db.base import Base, TimestampMixin
from anime_fortress.db.enums import (
    EpisodeStatus,
    MediaStatus,
    ProcessedStatus,
    QueueStatus,
    SourceStatus,
    TaskKind,
    TemplateKind,
    UploadStatus,
)


def _enum(enum_class: type) -> SAEnum:
    return SAEnum(
        enum_class,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


class Template(TimestampMixin, Base):
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    kind: Mapped[TemplateKind] = mapped_column(_enum(TemplateKind))
    body: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(String(255))
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)


class Source(TimestampMixin, Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    adapter: Mapped[str] = mapped_column(String(64), index=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[SourceStatus] = mapped_column(_enum(SourceStatus), default=SourceStatus.UNVERIFIED)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    anime: Mapped[list[Anime]] = relationship(back_populates="source")


class Channel(TimestampMixin, Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255))
    branding: Mapped[str] = mapped_column(String(120), default="")
    caption_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("templates.id", ondelete="SET NULL")
    )
    default_thumbnail_path: Mapped[str | None] = mapped_column(Text)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    can_post: Mapped[bool] = mapped_column(Boolean, default=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    caption_template: Mapped[Template | None] = relationship(foreign_keys=[caption_template_id])
    anime: Mapped[list[Anime]] = relationship(back_populates="channel")


class Anime(TimestampMixin, Base):
    __tablename__ = "anime"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    alternative_titles: Mapped[list[str]] = mapped_column(JSON, default=list)
    japanese_title: Mapped[str | None] = mapped_column(String(255))
    release_date: Mapped[date | None] = mapped_column(Date)
    release_year: Mapped[int | None] = mapped_column(Integer)
    anime_type: Mapped[str | None] = mapped_column("type", String(64))
    genres: Mapped[list[str]] = mapped_column(JSON, default=list)
    studio: Mapped[str | None] = mapped_column(String(255))
    rating: Mapped[float | None] = mapped_column(Float)
    rating_source: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str | None] = mapped_column(String(64))
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    age_rating: Mapped[str | None] = mapped_column(String(64))
    synopsis: Mapped[str | None] = mapped_column(Text)
    broadcast: Mapped[str | None] = mapped_column(String(255))
    episode_count: Mapped[int | None] = mapped_column(Integer)

    metadata_provider: Mapped[str | None] = mapped_column(String(64))
    metadata_external_id: Mapped[str | None] = mapped_column(String(120))
    metadata_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"))
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id", ondelete="RESTRICT"))
    announcement_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("templates.id", ondelete="SET NULL")
    )
    preferred_languages: Mapped[list[str] | None] = mapped_column(JSON)
    preferred_qualities: Mapped[list[str] | None] = mapped_column(JSON)
    include_extra_audio: Mapped[bool | None] = mapped_column(Boolean)
    default_thumbnail_path: Mapped[str | None] = mapped_column(Text)

    source: Mapped[Source | None] = relationship(back_populates="anime")
    channel: Mapped[Channel | None] = relationship(back_populates="anime")
    announcement_template: Mapped[Template | None] = relationship(
        foreign_keys=[announcement_template_id]
    )
    poster: Mapped[Poster | None] = relationship(
        back_populates="anime", uselist=False, cascade="all, delete-orphan"
    )
    seasons: Mapped[list[Season]] = relationship(
        back_populates="anime", cascade="all, delete-orphan", order_by="Season.number"
    )


class Poster(TimestampMixin, Base):
    __tablename__ = "posters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    anime_id: Mapped[int] = mapped_column(
        ForeignKey("anime.id", ondelete="CASCADE"), unique=True, index=True
    )
    path: Mapped[str] = mapped_column(Text)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))

    anime: Mapped[Anime] = relationship(back_populates="poster")


class Season(TimestampMixin, Base):
    __tablename__ = "seasons"
    __table_args__ = (UniqueConstraint("anime_id", "number", name="uq_season_anime_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    anime_id: Mapped[int] = mapped_column(ForeignKey("anime.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str | None] = mapped_column(String(255))
    episode_count_hint: Mapped[int | None] = mapped_column(Integer)

    anime: Mapped[Anime] = relationship(back_populates="seasons")
    episodes: Mapped[list[Episode]] = relationship(
        back_populates="season", cascade="all, delete-orphan", order_by="Episode.number"
    )


class Episode(TimestampMixin, Base):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("season_id", "number", name="uq_episode_season_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    anime_id: Mapped[int] = mapped_column(ForeignKey("anime.id", ondelete="CASCADE"), index=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[EpisodeStatus] = mapped_column(
        _enum(EpisodeStatus), default=EpisodeStatus.DISCOVERED, index=True
    )
    thumbnail_path: Mapped[str | None] = mapped_column(Text)
    skipped_reason: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    anime: Mapped[Anime] = relationship()
    season: Mapped[Season] = relationship(back_populates="episodes")
    source_media: Mapped[list[SourceMedia]] = relationship(
        back_populates="episode", cascade="all, delete-orphan"
    )
    processed_files: Mapped[list[ProcessedFile]] = relationship(
        back_populates="episode", cascade="all, delete-orphan"
    )


class SourceMedia(TimestampMixin, Base):
    __tablename__ = "source_media"
    __table_args__ = (
        UniqueConstraint(
            "episode_id", "adapter", "source_item_id", name="uq_source_media_item"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), index=True
    )
    adapter: Mapped[str] = mapped_column(String(64))
    source_item_id: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text)
    headers_env: Mapped[str | None] = mapped_column(String(120))
    quality_hint: Mapped[str | None] = mapped_column(String(32))
    container_hint: Mapped[str | None] = mapped_column(String(32))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    audio_languages_hint: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[MediaStatus] = mapped_column(
        _enum(MediaStatus), default=MediaStatus.DISCOVERED
    )
    last_error: Mapped[str | None] = mapped_column(Text)

    episode: Mapped[Episode] = relationship(back_populates="source_media")


class ProcessedFile(TimestampMixin, Base):
    __tablename__ = "processed_files"
    __table_args__ = (
        UniqueConstraint(
            "episode_id",
            "resolution_label",
            "audio_signature",
            name="uq_processed_episode_quality_audio",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), index=True
    )
    resolution_label: Mapped[str] = mapped_column(String(16), index=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    audio_signature: Mapped[str] = mapped_column(String(120))
    subtitle_languages: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[ProcessedStatus] = mapped_column(
        _enum(ProcessedStatus), default=ProcessedStatus.READY, index=True
    )
    telegram_file_id: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(Text)

    episode: Mapped[Episode] = relationship(back_populates="processed_files")
    audio_tracks: Mapped[list[AudioTrack]] = relationship(
        back_populates="processed_file", cascade="all, delete-orphan", order_by="AudioTrack.stream_index"
    )


class AudioTrack(Base):
    __tablename__ = "audio_tracks"
    __table_args__ = (
        UniqueConstraint("processed_file_id", "stream_index", name="uq_audio_stream"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    processed_file_id: Mapped[int] = mapped_column(
        ForeignKey("processed_files.id", ondelete="CASCADE"), index=True
    )
    stream_index: Mapped[int] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(64))
    language_code: Mapped[str | None] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(String(255))
    codec: Mapped[str | None] = mapped_column(String(64))
    channels: Mapped[int | None] = mapped_column(Integer)
    bit_rate: Mapped[int | None] = mapped_column(BigInteger)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    processed_file: Mapped[ProcessedFile] = relationship(back_populates="audio_tracks")


class QueueTask(TimestampMixin, Base):
    __tablename__ = "queue_tasks"
    __table_args__ = (
        Index("ix_queue_claim", "status", "priority", "available_at"),
        Index("ix_queue_dedupe", "dedupe_key", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[TaskKind] = mapped_column(_enum(TaskKind), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(255))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    status: Mapped[QueueStatus] = mapped_column(
        _enum(QueueStatus), default=QueueStatus.QUEUED, index=True
    )
    stage: Mapped[str] = mapped_column(String(80), default="")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UploadRecord(Base):
    __tablename__ = "upload_records"
    __table_args__ = (
        Index(
            "uq_upload_success_episode",
            "channel_id",
            "processed_file_id",
            unique=True,
            sqlite_where=text("status = 'success' AND processed_file_id IS NOT NULL"),
            postgresql_where=text("status = 'success' AND processed_file_id IS NOT NULL"),
        ),
        Index(
            "uq_upload_success_checksum",
            "channel_id",
            "file_sha256",
            unique=True,
            sqlite_where=text("status = 'success' AND file_sha256 IS NOT NULL"),
            postgresql_where=text("status = 'success' AND file_sha256 IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    anime_id: Mapped[int] = mapped_column(ForeignKey("anime.id", ondelete="SET NULL"))
    episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="SET NULL"), index=True
    )
    processed_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("processed_files.id", ondelete="SET NULL"), index=True
    )
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="RESTRICT"))
    resolution_label: Mapped[str | None] = mapped_column(String(16))
    file_name: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    caption: Mapped[str | None] = mapped_column(Text)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_file_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[UploadStatus] = mapped_column(_enum(UploadStatus))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AnnouncementRecord(Base):
    __tablename__ = "announcements"
    __table_args__ = (
        Index(
            "uq_announcement_success",
            "anime_id",
            "channel_id",
            unique=True,
            sqlite_where=text("status = 'success'"),
            postgresql_where=text("status = 'success'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    anime_id: Mapped[int] = mapped_column(ForeignKey("anime.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="RESTRICT"))
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("templates.id", ondelete="SET NULL")
    )
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    detail_message_id: Mapped[int | None] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    poster_path: Mapped[str | None] = mapped_column(Text)
    telegram_file_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[UploadStatus] = mapped_column(_enum(UploadStatus))
    error: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
