from __future__ import annotations

import pytest

from anime_fortress.errors import TemplateError
from anime_fortress.languages import (
    build_audio_label,
    canonical_language,
    dedupe_languages,
    iso639_2,
    normalize_language,
    order_languages,
)
from anime_fortress.naming import (
    normalize_quality,
    quality_from_height,
    render_filename,
    sanitize_caption_text,
    sanitize_filename_component,
)


def test_filename_sanitization_and_quality_mapping() -> None:
    assert sanitize_filename_component('Bad<>:"/\\|?* Name.') == "Bad Name"
    assert sanitize_filename_component("x" * 200, max_length=10) == "x" * 10
    assert quality_from_height(1080) == "1080p"
    assert quality_from_height(1084) == "1080p"
    assert quality_from_height(700) == "700p"
    assert quality_from_height(None) is None
    assert normalize_quality(" 720P ") == "720p"
    with pytest.raises(ValueError):
        normalize_quality("HD")


def test_render_filename_uses_pattern_and_enforces_extension() -> None:
    rendered = render_filename(
        "[S{season:02d}-Ep{episode:02d}] {anime_title} [{quality}] [{audio_label}] {branding}",
        anime_title="A/B: Title",
        season=1,
        episode=3,
        quality="1080p",
        audio_label="Dual",
        branding="@channel",
    )
    assert rendered == "[S01-Ep03] A B Title [1080p] [Dual] @channel.mkv"
    assert render_filename(
        "{anime_title}", anime_title="Show", season=1, episode=1, quality="720p",
        audio_label="Hindi",
    ) == "Show.mkv"


def test_render_filename_rejects_unknown_placeholders() -> None:
    with pytest.raises(TemplateError):
        render_filename(
            "{evil}", anime_title="Show", season=1, episode=1, quality="720p",
            audio_label="Hindi",
        )


def test_caption_sanitization() -> None:
    assert sanitize_caption_text(" hello\x00 ") == "hello"
    with pytest.raises(TemplateError):
        sanitize_caption_text("x" * 20, max_length=10)


def test_language_normalization_and_order() -> None:
    assert normalize_language("hin").name == "Hindi"
    assert normalize_language("hi-IN").name == "Hindi"
    assert normalize_language(None, "English 5.1").name == "English"
    assert normalize_language("und").known is False
    assert normalize_language("xx").known is False
    assert canonical_language("english") == "English"
    assert iso639_2("Hindi") == "hin"
    assert dedupe_languages(["Hindi", "hindi", "English", ""]) == ["Hindi", "English"]
    assert order_languages(["Japanese", "English", "Hindi"], ["Hindi", "English"]) == [
        "Hindi",
        "English",
        "Japanese",
    ]


def test_audio_label_is_truthful() -> None:
    assert build_audio_label([]) == "No Audio"
    assert build_audio_label(["Hindi"]) == "Hindi"
    assert build_audio_label(["Hindi", "English"]) == "Dual"
    assert build_audio_label(["Hindi", "English", "Tamil"]) == "Multi Audio"
    assert build_audio_label(["Hindi", "English", "Tamil"], style="joined") == "Hin-Eng-Tam"
    with pytest.raises(ValueError):
        canonical_language("Klingon")
