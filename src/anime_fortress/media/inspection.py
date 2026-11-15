from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from anime_fortress.config import Settings
from anime_fortress.errors import MediaInspectionError
from anime_fortress.languages import DetectedLanguage, normalize_language
from anime_fortress.naming import quality_from_height


@dataclass(frozen=True)
class VideoStream:
    index: int
    codec: str | None
    width: int | None
    height: int | None
    bit_rate: int | None = None
    attached_pic: bool = False


@dataclass(frozen=True)
class AudioStream:
    index: int
    codec: str | None
    language: DetectedLanguage
    title: str | None
    channels: int | None
    bit_rate: int | None
    is_default: bool


@dataclass(frozen=True)
class SubtitleStream:
    index: int
    codec: str | None
    language: DetectedLanguage
    title: str | None
    is_default: bool
    is_forced: bool


@dataclass
class MediaInfo:
    path: Path
    size_bytes: int
    container: str | None
    duration_seconds: float | None
    videos: list[VideoStream] = field(default_factory=list)
    audio: list[AudioStream] = field(default_factory=list)
    subtitles: list[SubtitleStream] = field(default_factory=list)

    @property
    def primary_video(self) -> VideoStream | None:
        normal = [stream for stream in self.videos if not stream.attached_pic]
        candidates = normal or self.videos
        if not candidates:
            return None
        return max(candidates, key=lambda stream: (stream.height or 0, stream.width or 0))

    @property
    def resolution_label(self) -> str | None:
        video = self.primary_video
        return quality_from_height(video.height if video else None)

    @property
    def audio_languages(self) -> list[str]:
        seen: list[str] = []
        for stream in self.audio:
            if stream.language.name not in seen:
                seen.append(stream.language.name)
        return seen

    @property
    def subtitle_languages(self) -> list[str]:
        seen: list[str] = []
        for stream in self.subtitles:
            language = stream.language.name
            if language not in seen:
                seen.append(language)
        return seen

    @property
    def is_image(self) -> bool:
        if not self.primary_video:
            return False
        container = (self.container or "").lower()
        return any(
            marker in container
            for marker in ("image", "png_pipe", "jpeg_pipe", "mjpeg", "webp", "bmp")
        )


class FFprobe:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def probe(self, path: Path) -> MediaInfo:
        path = Path(path)
        if not path.exists() or not path.is_file():
            raise MediaInspectionError(f"Media file does not exist: {path}")
        command = [
            self.settings.ffprobe_path,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                command,
                capture_output=True,
                timeout=120,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError as exc:
            raise MediaInspectionError(
                f"ffprobe was not found at '{self.settings.ffprobe_path}'. Install FFmpeg or set FFPROBE_PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise MediaInspectionError(f"ffprobe timed out while inspecting {path.name}") from exc
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", "replace")[-800:]
            raise MediaInspectionError(f"ffprobe could not read {path.name}: {stderr}")
        try:
            payload = json.loads(completed.stdout.decode("utf-8", "replace"))
        except ValueError as exc:
            raise MediaInspectionError(f"ffprobe returned invalid JSON for {path.name}") from exc
        return self._parse(path, payload)

    def _parse(self, path: Path, payload: dict[str, Any]) -> MediaInfo:
        if not isinstance(payload, dict):
            raise MediaInspectionError("ffprobe response was not an object")
        fmt = payload.get("format") or {}
        duration = self._float(fmt.get("duration"))
        videos: list[VideoStream] = []
        audio: list[AudioStream] = []
        subtitles: list[SubtitleStream] = []
        for stream in payload.get("streams") or []:
            if not isinstance(stream, dict):
                continue
            codec_type = stream.get("codec_type")
            tags = stream.get("tags") or {}
            disposition = stream.get("disposition") or {}
            index = int(stream.get("index", 0))
            if codec_type == "video":
                videos.append(
                    VideoStream(
                        index=index,
                        codec=stream.get("codec_name"),
                        width=self._int(stream.get("width")),
                        height=self._int(stream.get("height")),
                        bit_rate=self._int(stream.get("bit_rate")),
                        attached_pic=bool(disposition.get("attached_pic", 0)),
                    )
                )
            elif codec_type == "audio":
                raw_language = tags.get("language") or stream.get("language")
                title = tags.get("title")
                detected = normalize_language(
                    str(raw_language) if raw_language else None,
                    str(title) if title else None,
                )
                audio.append(
                    AudioStream(
                        index=index,
                        codec=stream.get("codec_name"),
                        language=detected,
                        title=str(title) if title else None,
                        channels=self._int(stream.get("channels")),
                        bit_rate=self._int(stream.get("bit_rate")),
                        is_default=bool(disposition.get("default", 0)),
                    )
                )
            elif codec_type == "subtitle":
                raw_language = tags.get("language")
                title = tags.get("title")
                subtitles.append(
                    SubtitleStream(
                        index=index,
                        codec=stream.get("codec_name"),
                        language=normalize_language(
                            str(raw_language) if raw_language else None,
                            str(title) if title else None,
                        ),
                        title=str(title) if title else None,
                        is_default=bool(disposition.get("default", 0)),
                        is_forced=bool(disposition.get("forced", 0)),
                    )
                )
        return MediaInfo(
            path=path,
            size_bytes=path.stat().st_size,
            container=fmt.get("format_name"),
            duration_seconds=duration,
            videos=videos,
            audio=audio,
            subtitles=subtitles,
        )

    @staticmethod
    def _int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
