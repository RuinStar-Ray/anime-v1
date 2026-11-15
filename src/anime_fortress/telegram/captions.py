from __future__ import annotations

from typing import Any

from anime_fortress.config import Settings
from anime_fortress.db.models import Anime, Channel, Episode, ProcessedFile, Season
from anime_fortress.languages import build_audio_label, dedupe_languages, order_languages


def _rating(value: float | None) -> str | None:
    return f"{value:.1f}" if value is not None else None


def announcement_context(
    anime: Anime,
    channel: Channel,
    *,
    settings: Settings,
    seasons_count: int | None,
    audio_languages: list[str],
    available_qualities: list[str],
    episode_count_discovered: int | None = None,
) -> dict[str, Any]:
    branding = channel.branding or settings.default_branding
    return {
        "anime_name": anime.title,
        "anime_title": anime.title,
        "alternative_titles": anime.alternative_titles or None,
        "japanese_title": anime.japanese_title,
        "release_date": anime.release_date.strftime("%d %b %Y") if anime.release_date else None,
        "release_year": anime.release_year,
        "seasons": seasons_count or None,
        "episode_count": anime.episode_count or episode_count_discovered,
        "type": anime.anime_type,
        "genres": anime.genres or None,
        "studio": anime.studio,
        "rating": _rating(anime.rating),
        "rating_source": anime.rating_source,
        "status": anime.status,
        "duration": f"{anime.duration_minutes} min" if anime.duration_minutes else None,
        "age_rating": anime.age_rating,
        "synopsis": anime.synopsis,
        "broadcast": anime.broadcast,
        "audio_languages": ", ".join(audio_languages) if audio_languages else None,
        "available_qualities": ", ".join(available_qualities) if available_qualities else None,
        "branding": branding,
        "channel_name": channel.title,
    }


def episode_caption_context(
    *,
    anime: Anime,
    channel: Channel,
    season: Season,
    episode: Episode,
    processed: ProcessedFile,
    filename: str,
    settings: Settings,
) -> dict[str, Any]:
    languages = [track.language for track in processed.audio_tracks]
    ordered = order_languages(dedupe_languages(languages), settings.preferred_languages)
    audio_label = build_audio_label(
        languages,
        style=settings.audio_label_style,
        dual_label=settings.dual_audio_label,
        multi_label=settings.multi_audio_label,
    )
    branding = channel.branding or settings.default_branding
    return {
        "anime_name": anime.title,
        "anime_title": anime.title,
        "alternative_titles": anime.alternative_titles or None,
        "japanese_title": anime.japanese_title,
        "release_year": anime.release_year,
        "release_date": anime.release_date.strftime("%d %b %Y") if anime.release_date else None,
        "type": anime.anime_type,
        "genres": anime.genres or None,
        "studio": anime.studio,
        "rating": _rating(anime.rating),
        "rating_source": anime.rating_source,
        "status": anime.status,
        "synopsis": anime.synopsis,
        "season": season.number,
        "episode": episode.number,
        "quality": processed.resolution_label,
        "audio_label": audio_label,
        "audio_languages": ", ".join(ordered) if ordered else None,
        "available_qualities": processed.resolution_label,
        "branding": branding,
        "channel_name": channel.title,
        "file_name": filename,
        "seasons": None,
        "episode_count": anime.episode_count,
        "duration": f"{anime.duration_minutes} min" if anime.duration_minutes else None,
        "age_rating": anime.age_rating,
        "broadcast": anime.broadcast,
        "year": anime.release_year,
    }
