from __future__ import annotations

import hashlib
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from anime_fortress.config import Settings
from anime_fortress.errors import SourceError
from anime_fortress.net import SafeHttpClient

_QUALITY_PATTERN = re.compile(r"(?i)\b(2160p|1440p|1080p|720p|576p|540p|480p|360p|240p)\b")


def extract_quality_hint(text: str | None) -> str | None:
    if not text:
        return None
    match = _QUALITY_PATTERN.search(text)
    return match.group(1).lower() if match else None


def stable_item_id(value: str, prefix: str = "item") -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


@dataclass(frozen=True)
class MediaLocation:
    item_id: str
    url: str | None = None
    local_path: str | None = None
    headers_env: str | None = None
    quality_hint: str | None = None
    container_hint: str | None = None
    size_hint: int | None = None
    audio_languages_hint: tuple[str, ...] = ()
    title_hint: str | None = None

    def __post_init__(self) -> None:
        if bool(self.url) == bool(self.local_path):
            raise ValueError("MediaLocation requires exactly one of url or local_path")


@dataclass(frozen=True)
class EpisodeCandidate:
    season: int
    number: int
    title: str | None
    media: tuple[MediaLocation, ...]


@dataclass
class DiscoveryResult:
    episodes: list[EpisodeCandidate]
    warnings: list[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    ok: bool
    message: str
    auth_required: bool = False
    direct_download: bool = False
    episodes_found: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceContext:
    anime_title: str
    anime_id: int | None = None
    source_name: str | None = None


@dataclass
class SourceRuntime:
    settings: Settings
    http: SafeHttpClient
    environ: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    allowed_library_roots: list[Path] = field(default_factory=list)


class SourceAdapter(ABC):
    name: ClassVar[str]
    description: ClassVar[str] = ""
    config_example: ClassVar[dict[str, Any]] = {}

    @abstractmethod
    async def validate(
        self, config: dict[str, Any], runtime: SourceRuntime
    ) -> ValidationResult:
        raise NotImplementedError

    @abstractmethod
    async def discover(
        self, config: dict[str, Any], context: SourceContext, runtime: SourceRuntime
    ) -> DiscoveryResult:
        raise NotImplementedError


def require_str(config: dict[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = config.get(key)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise SourceError(f"Source configuration requires a non-empty string '{key}'")
    return value.strip()


def optional_str(config: dict[str, Any], key: str) -> str | None:
    value = config.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SourceError(f"Source configuration field '{key}' must be a string")
    value = value.strip()
    return value or None


def positive_int(config: dict[str, Any], key: str, default: int | None = None) -> int:
    value = config.get(key, default)
    if value is None:
        raise SourceError(f"Source configuration requires '{key}'")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SourceError(f"Source configuration field '{key}' must be an integer") from exc
    if parsed <= 0:
        raise SourceError(f"Source configuration field '{key}' must be positive")
    return parsed


def bool_value(config: dict[str, Any], key: str, default: bool = False) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
