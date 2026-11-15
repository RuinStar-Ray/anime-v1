from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from anime_fortress.config import Settings
from anime_fortress.media.ffmpeg import FFmpeg
from anime_fortress.media.inspection import FFprobe
from anime_fortress.media.planning import build_plans
from anime_fortress.media.processor import MediaProcessor, SourceInput, sha256_file

pytestmark = pytest.mark.integration


def _ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _make_source(path: Path) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x240:rate=5:duration=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=880:duration=2",
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-map",
        "2:a",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-metadata:s:a:0",
        "language=hin",
        "-metadata:s:a:0",
        "title=Hindi",
        "-metadata:s:a:1",
        "language=eng",
        "-metadata:s:a:1",
        "title=English",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, check=False, timeout=120)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")


async def test_real_ffmpeg_multi_audio_remux(tmp_path: Path) -> None:
    if not _ffmpeg_available():
        pytest.skip("ffmpeg/ffprobe are not installed")
    settings = Settings(
        _env_file=None,
        app_env="test",
        data_dir=tmp_path / "data",
        ffmpeg_path=shutil.which("ffmpeg") or "ffmpeg",
        ffprobe_path=shutil.which("ffprobe") or "ffprobe",
        preferred_qualities=["240p"],
        preferred_languages=["Hindi", "English"],
        max_upload_mb=48,
    )
    settings.ensure_directories()
    source = tmp_path / "source.mkv"
    await asyncio.to_thread(_make_source, source)

    probe = FFprobe(settings)
    processor = MediaProcessor(settings, probe, FFmpeg(settings), http=object())  # type: ignore[arg-type]
    prepared = await processor.prepare_inputs(
        [SourceInput(source_media_id=1, url=None, local_path=str(source), headers_env=None, item_id="ep1")],
        tmp_path / "work",
    )
    assert len(prepared) == 1
    plans = build_plans(
        prepared,
        preferred_qualities=settings.preferred_qualities,
        preferred_languages=settings.preferred_languages,
    )
    assert len(plans) == 1
    plan = plans[0]
    assert plan.quality_label == "240p"
    assert [pick.language for pick in plan.audio] == ["Hindi", "English"]
    assert plan.audio[0].is_default is True
    assert plan.audio[1].is_default is False

    output = tmp_path / "out" / "episode.mkv"
    await processor.render(plan, output, title="Test Anime S01E01")
    info = await probe.probe(output)
    assert info.resolution_label == "240p"
    assert [stream.language.name for stream in info.audio] == ["Hindi", "English"]
    assert [(stream.language.name, bool(stream.is_default)) for stream in info.audio] == [
        ("Hindi", True),
        ("English", False),
    ]
    assert info.container is not None and "matroska" in info.container
    assert info.duration_seconds is not None and info.duration_seconds > 1.5
    assert output.stat().st_size > 0
    assert len(await asyncio.to_thread(sha256_file, output)) == 64


async def test_prepare_inputs_deduplicates_identical_files(tmp_path: Path) -> None:
    if not _ffmpeg_available():
        pytest.skip("ffmpeg/ffprobe are not installed")
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        ffmpeg_path=shutil.which("ffmpeg") or "ffmpeg",
        ffprobe_path=shutil.which("ffprobe") or "ffprobe",
    )
    source = tmp_path / "source.mkv"
    copy = tmp_path / "copy.mkv"
    await asyncio.to_thread(_make_source, source)
    await asyncio.to_thread(shutil.copyfile, source, copy)
    processor = MediaProcessor(
        settings, FFprobe(settings), FFmpeg(settings), http=object()  # type: ignore[arg-type]
    )
    prepared = await processor.prepare_inputs(
        [
            SourceInput(1, None, str(source), None, "one"),
            SourceInput(2, None, str(copy), None, "two"),
        ],
        tmp_path / "work",
    )
    assert len(prepared) == 1
