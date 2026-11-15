from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_ANNOUNCEMENT_TEMPLATE = """<b>{anime_name}</b>

╭──────────────────────
‣ SEASONS : {seasons}
‣ TYPE : {type}
‣ AUDIO : {audio_languages}
‣ QUALITY : {available_qualities}
‣ GENRE : {genres}
‣ AVERAGE RATING : {rating}
‣ STATUS : {status}
╰──────────────────────

<blockquote>‣ <b>Synopsis</b> :
{synopsis}</blockquote>"""

DEFAULT_FILENAME_PATTERN = (
    "[S{season:02d}-Ep{episode:02d}] {anime_title} [{quality}] [{audio_label}] {branding}.mkv"
)

KNOWN_AUDIO_LABEL_STYLES = {"multi", "joined"}


def split_csv(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            import json

            try:
                parsed = json.loads(stripped)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [part.strip() for part in stripped.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def split_paths(value: Any) -> list[Path]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            import json

            try:
                parsed = json.loads(stripped)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                return [Path(str(item)).expanduser() for item in parsed if str(item).strip()]
        separator = ";" if os.pathsep == ";" else ":"
        return [Path(part.strip()).expanduser() for part in stripped.split(separator) if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [Path(str(item)).expanduser() for item in value if str(item).strip()]
    return [Path(str(value)).expanduser()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Anime Fortress"
    app_env: Literal["development", "test", "production"] = "development"
    data_dir: Path = Path("data")
    database_url: str = ""

    bot_token: SecretStr | None = None
    telegram_api_base_url: str | None = None
    admin_user_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    default_channel_id: int | None = None
    default_branding: str = "@YourChannel"

    temp_dir: Path | None = None
    media_dir: Path | None = None
    posters_dir: Path | None = None
    thumbnails_dir: Path | None = None

    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    media_library_roots: Annotated[list[Path], NoDecode] = Field(default_factory=list)

    preferred_languages: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["Hindi", "English", "Japanese", "Tamil", "Telugu", "French"]
    )
    include_extra_audio: bool = True
    include_unknown_audio: bool = True
    include_subtitles: bool = True
    preferred_qualities: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["480p", "720p", "1080p"]
    )
    include_other_qualities: bool = False
    allow_silent_video: bool = False
    metadata_providers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["jikan", "anilist"]
    )
    audio_label_style: Literal["multi", "joined"] = "multi"
    dual_audio_label: str = "Dual"
    multi_audio_label: str = "Multi Audio"

    filename_pattern: str = DEFAULT_FILENAME_PATTERN
    caption_template: str = "{file_name}"
    announcement_template: str = DEFAULT_ANNOUNCEMENT_TEMPLATE
    missing_value_text: str = "N/A"
    cleanup_blank_lines: bool = True
    strict_episode_order: bool = True
    require_announcement: bool = True
    max_caption_length: int = 1024
    max_message_length: int = 4096
    max_document_filename_length: int = 120

    max_upload_mb: float = 49.0
    max_download_mb: int = 4096
    http_timeout_seconds: float = 30.0
    allow_private_networks: bool = False
    user_agent: str = "AnimeFortress/0.1 (+https://github.com/RuinStar-Ray/anime-v1)"

    worker_concurrency: int = 2
    worker_poll_interval_seconds: float = 2.0
    worker_stale_seconds: int = 900
    task_max_attempts: int = 5
    retry_base_seconds: int = 30
    retry_max_seconds: int = 3600
    cleanup_temp_files: bool = True
    temp_retention_hours: int = 24

    timezone: str = "UTC"
    log_level: str = "INFO"
    log_json: bool = False
    redact_secrets_in_logs: bool = True

    @field_validator("admin_user_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: Any) -> list[int]:
        if value in (None, ""):
            return []
        parts = split_csv(value)
        try:
            return sorted({int(part) for part in parts})
        except ValueError as exc:
            raise ValueError("ADMIN_USER_IDS must be a comma-separated list of numeric Telegram IDs") from exc

    @field_validator("preferred_languages", mode="before")
    @classmethod
    def _parse_languages(cls, value: Any) -> list[str]:
        return split_csv(value)

    @field_validator("preferred_qualities", mode="before")
    @classmethod
    def _parse_qualities(cls, value: Any) -> list[str]:
        return [item.lower() for item in split_csv(value)]

    @field_validator("metadata_providers", mode="before")
    @classmethod
    def _parse_metadata_providers(cls, value: Any) -> list[str]:
        return [item.lower() for item in split_csv(value)] or ["jikan", "anilist"]

    @field_validator("media_library_roots", mode="before")
    @classmethod
    def _parse_roots(cls, value: Any) -> list[Path]:
        return split_paths(value)

    @field_validator("audio_label_style")
    @classmethod
    def _validate_label_style(cls, value: str) -> str:
        if value not in KNOWN_AUDIO_LABEL_STYLES:
            raise ValueError(f"AUDIO_LABEL_STYLE must be one of {sorted(KNOWN_AUDIO_LABEL_STYLES)}")
        return value

    @field_validator("max_upload_mb")
    @classmethod
    def _validate_upload_limit(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("MAX_UPLOAD_MB must be positive")
        return value

    @field_validator("worker_concurrency")
    @classmethod
    def _validate_concurrency(cls, value: int) -> int:
        if not 1 <= value <= 16:
            raise ValueError("WORKER_CONCURRENCY must be between 1 and 16")
        return value

    @model_validator(mode="after")
    def _finalize_paths(self) -> Settings:
        if not self.bot_token:
            self.bot_token = None
        if not self.database_url:
            db_path = (self.data_dir / "anime.db").resolve()
            self.database_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        if self.temp_dir is None:
            self.temp_dir = self.data_dir / "tmp"
        if self.media_dir is None:
            self.media_dir = self.data_dir / "media"
        if self.posters_dir is None:
            self.posters_dir = self.data_dir / "posters"
        if self.thumbnails_dir is None:
            self.thumbnails_dir = self.data_dir / "thumbnails"
        self.data_dir = self.data_dir.resolve()
        self.temp_dir = self.temp_dir.resolve()
        self.media_dir = self.media_dir.resolve()
        self.posters_dir = self.posters_dir.resolve()
        self.thumbnails_dir = self.thumbnails_dir.resolve()
        self.media_library_roots = [root.resolve() for root in self.media_library_roots]
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown TIMEZONE: {self.timezone}") from exc
        deduped: list[str] = []
        for language in self.preferred_languages:
            if language.lower() not in {item.lower() for item in deduped}:
                deduped.append(language)
        self.preferred_languages = deduped
        quality_seen: list[str] = []
        for quality in self.preferred_qualities:
            if quality not in quality_seen:
                quality_seen.append(quality)
        self.preferred_qualities = quality_seen
        return self

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.temp_dir,
            self.media_dir,
            self.posters_dir,
            self.thumbnails_dir,
        ):
            assert path is not None
            path.mkdir(parents=True, exist_ok=True)

    def require_bot_token(self) -> SecretStr:
        if not self.bot_token or not self.bot_token.get_secret_value().strip():
            raise RuntimeError("BOT_TOKEN is not configured. Set it in the environment or .env file.")
        return self.bot_token

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_user_ids


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
