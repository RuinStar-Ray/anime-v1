from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Sequence
from pathlib import Path

from anime_fortress.config import Settings
from anime_fortress.errors import MediaProcessingError


class FFmpeg:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def run(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: int = 3600,
        operation: str = "ffmpeg",
    ) -> None:
        command = [self.settings.ffmpeg_path, "-hide_banner", "-nostdin", "-y", *map(str, args)]
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                command,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError as exc:
            raise MediaProcessingError(
                f"ffmpeg was not found at '{self.settings.ffmpeg_path}'. Install FFmpeg or set FFMPEG_PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise MediaProcessingError(f"{operation} timed out after {timeout_seconds} seconds") from exc
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", "replace").strip()
            tail = "\n".join(stderr.splitlines()[-15:])
            raise MediaProcessingError(f"{operation} failed: {tail or 'unknown ffmpeg error'}")

    async def extract_thumbnail(
        self, video: Path, output: Path, *, seek_seconds: float = 5.0, quality: int = 4
    ) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        args = [
            "-ss",
            str(max(seek_seconds, 0)),
            "-i",
            video,
            "-frames:v",
            "1",
            "-vf",
            "scale=320:320:force_original_aspect_ratio=decrease",
            "-q:v",
            str(quality),
            output,
        ]
        try:
            await self.run(args, timeout_seconds=120, operation="thumbnail extraction")
        except MediaProcessingError:
            if seek_seconds <= 0:
                raise
            await self.run(
                [
                    "-ss",
                    "0",
                    "-i",
                    video,
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=320:320:force_original_aspect_ratio=decrease",
                    "-q:v",
                    str(quality),
                    output,
                ],
                timeout_seconds=120,
                operation="thumbnail extraction",
            )

    async def convert_image(
        self, source: Path, output: Path, *, max_dimension: int = 320, quality: int = 4
    ) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        await self.run(
            [
                "-i",
                source,
                "-frames:v",
                "1",
                "-vf",
                f"scale={max_dimension}:{max_dimension}:force_original_aspect_ratio=decrease",
                "-q:v",
                str(quality),
                output,
            ],
            timeout_seconds=120,
            operation="image conversion",
        )

    async def normalize_poster(self, source: Path, output: Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        await self.run(
            [
                "-i",
                source,
                "-frames:v",
                "1",
                "-vf",
                "scale='min(1280,iw)':-2",
                "-q:v",
                "2",
                output,
            ],
            timeout_seconds=180,
            operation="poster conversion",
        )
