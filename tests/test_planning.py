from __future__ import annotations

from pathlib import Path

import pytest

from anime_fortress.errors import MediaProcessingError
from anime_fortress.languages import normalize_language
from anime_fortress.media.inspection import (
    AudioStream,
    MediaInfo,
    SubtitleStream,
    VideoStream,
)
from anime_fortress.media.planning import PreparedInput, build_plans


def _audio(
    index: int,
    code: str,
    *,
    title: str | None = None,
    channels: int = 2,
    bit_rate: int = 128000,
) -> AudioStream:
    return AudioStream(
        index=index,
        codec=code,
        language=normalize_language(code if code in {"hin", "eng", "fre"} else None, title),
        title=title,
        channels=channels,
        bit_rate=bit_rate,
        is_default=False,
    )


def _input(
    height: int,
    audio: list[AudioStream],
    *,
    name: str = "source.mkv",
    video_bit_rate: int = 1_000_000,
    subtitles: list[SubtitleStream] | None = None,
) -> PreparedInput:
    info = MediaInfo(
        path=Path(name),
        size_bytes=height * 1000,
        container="matroska",
        duration_seconds=60.0,
        videos=[
            VideoStream(
                index=0,
                codec="h264",
                width=(height * 16) // 9,
                height=height,
                bit_rate=video_bit_rate,
            )
        ],
        audio=audio,
        subtitles=subtitles or [],
    )
    return PreparedInput(source_media_id=None, path=Path(name), info=info)


def _subtitles(language: str, *, forced: bool = False) -> SubtitleStream:
    return SubtitleStream(
        index=3,
        codec="subrip",
        language=normalize_language(language),
        title=language,
        is_default=False,
        is_forced=forced,
    )


def test_preferred_quality_plans_never_upscale() -> None:
    inputs = [_input(480, [_audio(1, "hin")]), _input(1080, [_audio(1, "hin")])]
    plans = build_plans(
        inputs,
        preferred_qualities=["1080p", "480p"],
        preferred_languages=["Hindi"],
    )
    assert [plan.quality_label for plan in plans] == ["480p", "1080p"]
    assert [plan.height for plan in plans] == [480, 1080]


def test_missing_preferred_quality_raises_instead_of_upscaling() -> None:
    with pytest.raises(MediaProcessingError, match="Upscaling"):
        build_plans(
            [_input(480, [_audio(1, "hin")])],
            preferred_qualities=["720p"],
            preferred_languages=["Hindi"],
        )


def test_other_qualities_are_optional() -> None:
    inputs = [_input(480, [_audio(1, "hin")]), _input(360, [_audio(1, "hin")])]
    only_preferred = build_plans(
        inputs, preferred_qualities=["480p"], preferred_languages=["Hindi"]
    )
    assert [plan.quality_label for plan in only_preferred] == ["480p"]
    including_other = build_plans(
        inputs,
        preferred_qualities=["480p"],
        include_other_qualities=True,
        preferred_languages=["Hindi"],
    )
    assert [plan.quality_label for plan in including_other] == ["360p", "480p"]


def test_audio_dedupe_keeps_distinct_codecs_and_prefers_higher_bitrate() -> None:
    aac_hindi = _audio(1, "aac", title="Hindi", bit_rate=128000)
    ac3_hindi = _audio(2, "ac3", title="Hindi", bit_rate=384000)
    duplicate_aac = _audio(4, "aac", title="Hindi", bit_rate=256000)
    plan = build_plans(
        [
            _input(720, [aac_hindi, ac3_hindi], name="one.mkv"),
            _input(720, [duplicate_aac], name="two.mkv", video_bit_rate=2_000_000),
        ],
        preferred_qualities=["720p"],
        preferred_languages=["Hindi"],
    )[0]
    assert len(plan.audio) == 2
    assert {pick.stream.codec for pick in plan.audio} == {"aac", "ac3"}
    aac_pick = next(pick for pick in plan.audio if pick.stream.codec == "aac")
    assert aac_pick.stream.bit_rate == 256000
    assert plan.audio[0].is_default is True
    assert sum(1 for pick in plan.audio if pick.is_default) == 1


def test_extra_and_unknown_audio_can_be_excluded() -> None:
    unknown = _audio(1, "zzz", title="Commentary")
    plan = build_plans(
        [_input(720, [_audio(1, "hin"), _audio(2, "fre", title="French"), unknown])],
        preferred_qualities=["720p"],
        preferred_languages=["Hindi"],
        include_extra_audio=False,
        include_unknown_audio=False,
    )[0]
    assert [pick.language for pick in plan.audio] == ["Hindi"]


def test_no_audio_fails_unless_silent_video_allowed() -> None:
    with pytest.raises(MediaProcessingError, match="No usable audio"):
        build_plans(
            [_input(720, [])], preferred_qualities=["720p"], preferred_languages=["Hindi"]
        )
    plan = build_plans(
        [_input(720, [])],
        preferred_qualities=["720p"],
        preferred_languages=["Hindi"],
        allow_silent_video=True,
    )[0]
    assert plan.audio == []


def test_subtitles_dedupe_and_signature_changes_with_codec() -> None:
    plan = build_plans(
        [
            _input(
                720,
                [_audio(1, "hin")],
                subtitles=[_subtitles("English"), _subtitles("English")],
            ),
            _input(
                1080,
                [_audio(1, "hin")],
                subtitles=[_subtitles("English", forced=True)],
            ),
        ],
        preferred_qualities=["720p"],
        preferred_languages=["Hindi"],
    )[0]
    assert len(plan.subtitles) == 2
    assert plan.subtitle_languages == ["English"]

    first = build_plans(
        [_input(720, [_audio(1, "aac", title="Hindi")])],
        preferred_qualities=["720p"],
        preferred_languages=["Hindi"],
    )[0]
    second = build_plans(
        [_input(720, [_audio(1, "ac3", title="Hindi")])],
        preferred_qualities=["720p"],
        preferred_languages=["Hindi"],
    )[0]
    assert first.audio_signature != second.audio_signature


def test_no_video_streams_raise() -> None:
    empty = PreparedInput(
        source_media_id=None,
        path=Path("audio.mka"),
        info=MediaInfo(
            path=Path("audio.mka"),
            size_bytes=10,
            container="matroska",
            duration_seconds=1.0,
            audio=[_audio(1, "hin")],
        ),
    )
    with pytest.raises(MediaProcessingError, match="No video streams"):
        build_plans(
            [empty], preferred_qualities=["720p"], preferred_languages=["Hindi"]
        )
