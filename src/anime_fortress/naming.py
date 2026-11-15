from __future__ import annotations

import re
import string
from typing import Any

from anime_fortress.errors import TemplateError

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_QUALITY_RE = re.compile(r"^\d{3,4}p$")
ALLOWED_FILENAME_FIELDS = {
    "season",
    "episode",
    "anime_title",
    "quality",
    "audio_label",
    "branding",
    "year",
    "type",
}


def sanitize_filename_component(value: Any, *, max_length: int = 100) -> str:
    text = str(value or "")
    text = _INVALID_FILENAME_CHARS.sub(" ", text)
    text = text.replace("\r", " ").replace("\n", " ")
    text = _MULTI_SPACE.sub(" ", text).strip(" .")
    return text[:max_length].strip(" .")


def normalize_quality(value: str) -> str:
    quality = value.strip().lower()
    if not _QUALITY_RE.match(quality):
        raise ValueError(f"Invalid quality label: {value!r}; expected a label such as 720p")
    return quality


def quality_from_height(height: int | None) -> str | None:
    if not height or height <= 0:
        return None
    standard = (144, 240, 360, 480, 540, 576, 720, 1080, 1440, 2160, 4320)
    nearest = min(standard, key=lambda candidate: abs(candidate - height))
    if abs(nearest - height) <= 12:
        return f"{nearest}p"
    return f"{height}p"


def format_season_episode(season: int, episode: int) -> str:
    return f"S{season:02d}-Ep{episode:02d}"


def _validate_pattern(pattern: str) -> None:
    try:
        parsed = list(string.Formatter().parse(pattern))
    except ValueError as exc:
        raise TemplateError(f"Invalid filename pattern: {exc}") from exc
    for _, field_name, _, _ in parsed:
        if field_name is None:
            continue
        root = field_name.split(".", 1)[0].split("[", 1)[0]
        if root not in ALLOWED_FILENAME_FIELDS:
            raise TemplateError(
                f"Unknown filename placeholder {{{root}}}. "
                f"Allowed: {', '.join(sorted(ALLOWED_FILENAME_FIELDS))}"
            )


def _safe_values(values: dict[str, Any]) -> dict[str, Any]:
    prepared: dict[str, Any] = {}
    for key in ALLOWED_FILENAME_FIELDS:
        value = values.get(key, "")
        if key in {"season", "episode"}:
            prepared[key] = int(value) if value not in (None, "") else 0
        elif key == "year":
            prepared[key] = str(value or "")
        elif key == "quality":
            prepared[key] = normalize_quality(str(value)) if value else ""
        else:
            prepared[key] = sanitize_filename_component(value)
    return prepared


def render_filename(
    pattern: str,
    *,
    anime_title: str,
    season: int,
    episode: int,
    quality: str,
    audio_label: str,
    branding: str = "",
    year: int | None = None,
    anime_type: str | None = None,
    max_length: int = 120,
) -> str:
    _validate_pattern(pattern)
    values = _safe_values(
        {
            "season": season,
            "episode": episode,
            "anime_title": anime_title,
            "quality": quality,
            "audio_label": audio_label,
            "branding": branding,
            "year": year,
            "type": anime_type,
        }
    )
    try:
        rendered = pattern.format(**values)
    except (KeyError, ValueError, IndexError) as exc:
        raise TemplateError(f"Could not render filename pattern: {exc}") from exc
    rendered = _INVALID_FILENAME_CHARS.sub(" ", rendered)
    rendered = _MULTI_SPACE.sub(" ", rendered).strip(" .")
    lowered = rendered.lower()
    if not lowered.endswith((".mkv", ".mp4", ".mka")):
        rendered = f"{rendered}.mkv"
    if len(rendered) > max_length:
        stem, dot, extension = rendered.rpartition(".")
        if dot and len(extension) <= 5:
            keep = max_length - len(extension) - 1
            rendered = f"{stem[:keep].rstrip(' .')}.{extension}"
        else:
            rendered = rendered[:max_length].rstrip(" .")
    return rendered


def sanitize_caption_text(text: str, max_length: int = 1024) -> str:
    cleaned = text.replace("\x00", "").strip()
    if len(cleaned) > max_length:
        raise TemplateError(
            f"Caption is {len(cleaned)} characters; Telegram allows {max_length}. "
            "Shorten the caption template."
        )
    return cleaned
