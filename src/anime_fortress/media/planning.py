from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from anime_fortress.errors import MediaProcessingError
from anime_fortress.languages import canonical_language, dedupe_languages, order_languages
from anime_fortress.media.inspection import AudioStream, MediaInfo, SubtitleStream, VideoStream


@dataclass
class PreparedInput:
    source_media_id: int | None
    path: Path
    info: MediaInfo

    @property
    def resolution_label(self) -> str | None:
        return self.info.resolution_label


@dataclass
class AudioPick:
    input_ref: PreparedInput
    stream: AudioStream
    language: str
    is_default: bool = False


@dataclass
class SubtitlePick:
    input_ref: PreparedInput
    stream: SubtitleStream
    language: str


@dataclass
class OutputPlan:
    quality_label: str
    width: int
    height: int
    primary: PreparedInput
    video: VideoStream
    audio: list[AudioPick] = field(default_factory=list)
    subtitles: list[SubtitlePick] = field(default_factory=list)

    @property
    def audio_languages(self) -> list[str]:
        names: list[str] = []
        for pick in self.audio:
            if pick.language not in names:
                names.append(pick.language)
        return names

    @property
    def subtitle_languages(self) -> list[str]:
        return dedupe_languages([pick.language for pick in self.subtitles])

    @property
    def audio_signature(self) -> str:
        signature = "|".join(
            f"{pick.language}:{(pick.stream.title or '').lower()}:{pick.stream.channels}:"
            f"{(pick.stream.codec or '').lower()}:{'d' if pick.is_default else 'n'}"
            for pick in self.audio
        )
        return hashlib.sha1(signature.encode("utf-8")).hexdigest()[:16]


def _quality_sort_key(label: str) -> tuple[int, str]:
    digits = "".join(character for character in label if character.isdigit())
    return (int(digits) if digits else 0, label)


def _choose_primary(candidates: list[PreparedInput], preferred: list[str]) -> PreparedInput:
    def score(prepared: PreparedInput) -> tuple[int, int, int]:
        matched = sum(
            1
            for stream in prepared.info.audio
            if stream.language.known and stream.language.name in preferred
        )
        video = prepared.info.primary_video
        return (matched, video.bit_rate if video and video.bit_rate else 0, prepared.info.size_bytes)

    return max(candidates, key=score)


def _audio_key(stream: AudioStream) -> tuple[str, int | None, str, str]:
    return (
        stream.language.name.lower(),
        stream.channels,
        (stream.title or "").strip().lower(),
        (stream.codec or "").lower(),
    )


def _audio_rank(stream: AudioStream, preference_index: dict[str, int]) -> tuple[int, int, int]:
    preference = preference_index.get(stream.language.name.lower(), len(preference_index))
    return (preference, -(stream.channels or 0), -(stream.bit_rate or 0))


def build_plans(
    inputs: list[PreparedInput],
    *,
    preferred_qualities: list[str],
    include_other_qualities: bool = False,
    preferred_languages: list[str],
    include_extra_audio: bool = True,
    include_unknown_audio: bool = True,
    include_subtitles: bool = True,
    allow_silent_video: bool = False,
) -> list[OutputPlan]:
    if not inputs:
        raise MediaProcessingError("No media inputs are available for planning")
    available: dict[str, list[PreparedInput]] = {}
    for prepared in inputs:
        label = prepared.resolution_label
        if not label or not prepared.info.primary_video:
            continue
        available.setdefault(label, []).append(prepared)
    if not available:
        raise MediaProcessingError("No video streams were detected in the source media")
    targets = [quality for quality in preferred_qualities if quality in available]
    if include_other_qualities:
        targets.extend(
            label
            for label in available
            if label not in targets and label not in preferred_qualities
        )
    targets = sorted(dict.fromkeys(targets), key=_quality_sort_key)
    if not targets:
        raise MediaProcessingError(
            "None of the preferred qualities are natively available. "
            f"Preferred: {', '.join(preferred_qualities)}; detected: "
            f"{', '.join(sorted(available, key=_quality_sort_key))}. "
            "Upscaling is never performed."
        )
    canonical_preferred = []
    for language in preferred_languages:
        try:
            canonical_preferred.append(canonical_language(language))
        except ValueError:
            canonical_preferred.append(language)
    preference_index = {name.lower(): index for index, name in enumerate(canonical_preferred)}
    plans: list[OutputPlan] = []
    for target in targets:
        primary = _choose_primary(available[target], canonical_preferred)
        video = primary.info.primary_video
        assert video is not None
        picks: dict[tuple[str, int | None, str, str], AudioPick] = {}
        for prepared in inputs:
            for stream in prepared.info.audio:
                language = stream.language.name
                known = stream.language.known
                if known and language.lower() in preference_index:
                    allowed = True
                elif known:
                    allowed = include_extra_audio
                else:
                    allowed = include_unknown_audio
                if not allowed:
                    continue
                key = _audio_key(stream)
                existing = picks.get(key)
                if existing is None:
                    picks[key] = AudioPick(input_ref=prepared, stream=stream, language=language)
                else:
                    current = (existing.stream.bit_rate or 0, existing.input_ref.info.primary_video.height or 0)
                    challenger = (stream.bit_rate or 0, prepared.info.primary_video.height or 0)
                    if challenger > current:
                        picks[key] = AudioPick(input_ref=prepared, stream=stream, language=language)
        ordered_audio = sorted(
            picks.values(), key=lambda pick: _audio_rank(pick.stream, preference_index)
        )
        if not ordered_audio and not allow_silent_video:
            raise MediaProcessingError(
                "No usable audio tracks were found. Set ALLOW_SILENT_VIDEO=true only if intentional."
            )
        default_index = 0
        for index, pick in enumerate(ordered_audio):
            if pick.language.lower() in preference_index:
                default_index = index
                break
        for index, pick in enumerate(ordered_audio):
            pick.is_default = index == default_index
        subtitle_picks: dict[tuple[str, str, bool], SubtitlePick] = {}
        if include_subtitles:
            for prepared in sorted(
                inputs,
                key=lambda item: item.info.primary_video.height
                if item.info.primary_video and item.info.primary_video.height
                else 0,
                reverse=True,
            ):
                for stream in prepared.info.subtitles:
                    key = (
                        stream.language.name.lower(),
                        (stream.title or "").strip().lower(),
                        stream.is_forced,
                    )
                    if key not in subtitle_picks:
                        subtitle_picks[key] = SubtitlePick(
                            input_ref=prepared, stream=stream, language=stream.language.name
                        )
        plans.append(
            OutputPlan(
                quality_label=target,
                width=video.width or 0,
                height=video.height or 0,
                primary=primary,
                video=video,
                audio=ordered_audio,
                subtitles=list(subtitle_picks.values()),
            )
        )
    return plans


def ordered_languages(languages: list[str], preferred: list[str]) -> list[str]:
    canonical = []
    for language in preferred:
        try:
            canonical.append(canonical_language(language))
        except ValueError:
            canonical.append(language)
    return order_languages(languages, canonical)
