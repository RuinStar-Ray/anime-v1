from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anime_fortress.config import Settings
from anime_fortress.errors import (
    MediaInspectionError,
    MediaProcessingError,
    UploadTooLarge,
)
from anime_fortress.languages import iso639_2
from anime_fortress.media.ffmpeg import FFmpeg
from anime_fortress.media.inspection import FFprobe, MediaInfo
from anime_fortress.media.planning import OutputPlan, PreparedInput
from anime_fortress.naming import sanitize_filename_component
from anime_fortress.net import SafeHttpClient, headers_from_env, sanitize_url

logger = logging.getLogger(__name__)
_LANGUAGE_TAG_RE = re.compile(r"^[a-z]{2,3}$")


def _language_tag(language: str, raw_code: str | None) -> str:
    code = (raw_code or "").strip().lower()
    if code and _LANGUAGE_TAG_RE.match(code):
        return code
    return iso639_2(language) or "und"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class SourceInput:
    source_media_id: int | None
    url: str | None
    local_path: str | None
    headers_env: str | None
    item_id: str
    title: str | None = None


class MediaProcessor:
    def __init__(
        self,
        settings: Settings,
        probe: FFprobe,
        ffmpeg: FFmpeg,
        http: SafeHttpClient,
    ) -> None:
        self.settings = settings
        self.probe = probe
        self.ffmpeg = ffmpeg
        self.http = http

    async def prepare_inputs(
        self, sources: list[SourceInput], workdir: Path
    ) -> list[PreparedInput]:
        workdir.mkdir(parents=True, exist_ok=True)
        prepared: list[PreparedInput] = []
        seen_paths: set[Path] = set()
        seen_hashes: set[str] = set()
        for index, source in enumerate(sources):
            if source.local_path:
                path = Path(source.local_path).expanduser().resolve()
                if not path.exists() or not path.is_file():
                    raise MediaProcessingError(f"Local source file no longer exists: {path}")
            elif source.url:
                suffix = Path(source.url.split("?", 1)[0]).suffix
                if len(suffix) > 6 or not suffix:
                    suffix = ".bin"
                target = workdir / f"source-{index}-{sanitize_filename_component(source.item_id, max_length=40)}{suffix}"
                headers = headers_from_env(source.headers_env)
                logger.info("Downloading %s", sanitize_url(source.url))
                downloaded = await self.http.download(source.url, target, headers=headers)
                path = downloaded.path
            else:
                raise MediaProcessingError("Source media has neither a URL nor a local path")
            if path in seen_paths:
                logger.info("Skipping duplicate input path %s", path.name)
                continue
            seen_paths.add(path)
            try:
                info = await self.probe.probe(path)
            except MediaInspectionError as exc:
                raise MediaProcessingError(f"Could not probe {path.name}: {exc}") from exc
            if info.primary_video is None:
                raise MediaProcessingError(f"Source file has no video stream: {path.name}")
            digest = await asyncio.to_thread(sha256_file, path)
            if digest in seen_hashes:
                logger.info("Skipping byte-identical input %s", path.name)
                continue
            seen_hashes.add(digest)
            prepared.append(
                PreparedInput(source_media_id=source.source_media_id, path=path, info=info)
            )
        if not prepared:
            raise MediaProcessingError("No unique media inputs could be prepared")
        return prepared

    async def render(
        self,
        plan: OutputPlan,
        output_path: Path,
        *,
        cover_path: Path | None = None,
        title: str | None = None,
    ) -> None:
        input_files: list[Path] = [plan.primary.path]
        refs: dict[Path, PreparedInput] = {plan.primary.path: plan.primary}

        def add_ref(reference: PreparedInput) -> int:
            if reference.path not in refs:
                refs[reference.path] = reference
                input_files.append(reference.path)
            return input_files.index(reference.path)

        for pick in plan.audio:
            add_ref(pick.input_ref)
        for pick in plan.subtitles:
            add_ref(pick.input_ref)

        args: list[str] = []
        for input_file in input_files:
            args.extend(["-i", str(input_file)])
        args.extend(["-map", f"0:{plan.video.index}"])
        for pick in plan.audio:
            input_index = add_ref(pick.input_ref)
            args.extend(["-map", f"{input_index}:{pick.stream.index}"])
        for pick in plan.subtitles:
            input_index = add_ref(pick.input_ref)
            args.extend(["-map", f"{input_index}:{pick.stream.index}"])
        args.extend(["-c", "copy", "-map_metadata", "-1"])
        if title:
            args.extend(["-metadata", f"title={title}"])
        for output_index, pick in enumerate(plan.audio):
            tag = _language_tag(pick.language, pick.stream.language.code)
            track_title = pick.stream.title or pick.language
            args.extend(
                [
                    f"-metadata:s:a:{output_index}",
                    f"language={tag}",
                    f"-metadata:s:a:{output_index}",
                    f"title={track_title}",
                    f"-disposition:a:{output_index}",
                    "default" if pick.is_default else "0",
                ]
            )
        for output_index, pick in enumerate(plan.subtitles):
            tag = _language_tag(pick.language, pick.stream.language.code)
            track_title = pick.stream.title or pick.language
            args.extend(
                [
                    f"-metadata:s:s:{output_index}",
                    f"language={tag}",
                    f"-metadata:s:s:{output_index}",
                    f"title={track_title}",
                    f"-disposition:s:{output_index}",
                    "forced" if pick.stream.is_forced else "0",
                ]
            )
        if cover_path and cover_path.exists():
            args.extend(
                [
                    "-attach",
                    str(cover_path),
                    "-metadata:s:t:0",
                    "mimetype=image/jpeg",
                    "-metadata:s:t:0",
                    "filename=cover.jpg",
                ]
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        args.extend(["-f", "matroska", str(output_path)])
        await self.ffmpeg.run(args, operation=f"{plan.quality_label} remux")
        await self.verify(plan, output_path)

    async def verify(self, plan: OutputPlan, output_path: Path) -> MediaInfo:
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise MediaProcessingError("ffmpeg produced no output file")
        info = await self.probe.probe(output_path)
        video = info.primary_video
        if video is None:
            raise MediaProcessingError("Processed file has no video stream")
        if (video.height or 0) != plan.height or (video.width or 0) != plan.width:
            raise MediaProcessingError(
                f"Processed resolution {video.width}x{video.height} does not match "
                f"planned {plan.width}x{plan.height}"
            )
        expected_audio = [pick.language for pick in plan.audio]
        actual_audio = [stream.language.name for stream in info.audio]
        if actual_audio != expected_audio:
            raise MediaProcessingError(
                f"Audio language mismatch after mux; expected {expected_audio}, got {actual_audio}"
            )
        if len(info.audio) != len(plan.audio):
            raise MediaProcessingError(
                f"Audio track count mismatch after mux; expected {len(plan.audio)}, "
                f"got {len(info.audio)}"
            )
        if plan.subtitles and len(info.subtitles) != len(plan.subtitles):
            raise MediaProcessingError("Subtitle track count mismatch after mux")
        source_duration = plan.primary.info.duration_seconds
        if source_duration and info.duration_seconds:
            tolerance = max(3.0, source_duration * 0.02)
            if abs(info.duration_seconds - source_duration) > tolerance:
                raise MediaProcessingError(
                    f"Processed duration {info.duration_seconds:.1f}s differs from source "
                    f"{source_duration:.1f}s"
                )
        if info.size_bytes > self.settings.max_upload_bytes:
            logger.warning(
                "Processed file %s is %.1f MB, above MAX_UPLOAD_MB=%.1f; publishing will fail "
                "until the limit or Telegram API setup is adjusted",
                output_path.name,
                info.size_bytes / (1024 * 1024),
                self.settings.max_upload_mb,
            )
        return info

    async def make_telegram_thumbnail(
        self, video: Path, output: Path, *, custom_image: Path | None = None
    ) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        if custom_image and custom_image.exists():
            await self.ffmpeg.convert_image(custom_image, output, max_dimension=320, quality=5)
        else:
            await self.ffmpeg.extract_thumbnail(video, output, seek_seconds=5, quality=5)
        limit = 200 * 1024
        attempts = [output]
        quality = 8
        while output.stat().st_size > limit and quality <= 20:
            await self.ffmpeg.convert_image(output, output.with_suffix(".retry.jpg"), max_dimension=320, quality=quality)
            output.unlink(missing_ok=True)
            output.with_suffix(".retry.jpg").replace(output)
            attempts.append(output)
            quality += 4
        if output.stat().st_size > limit:
            raise MediaProcessingError(
                "Could not generate a Telegram thumbnail under 200 KB; provide a simpler custom image"
            )
        return output

    def ensure_uploadable(self, path: Path) -> None:
        size = path.stat().st_size
        if size > self.settings.max_upload_bytes:
            raise UploadTooLarge(
                f"File is {size / (1024 * 1024):.1f} MB, above MAX_UPLOAD_MB="
                f"{self.settings.max_upload_mb:.1f}. Use a local Bot API server or lower the quality."
            )


def build_source_input(media: Any) -> SourceInput:
    return SourceInput(
        source_media_id=media.id,
        url=media.source_url,
        local_path=media.local_path,
        headers_env=media.headers_env,
        item_id=media.source_item_id,
        title=media.source_item_id,
    )
