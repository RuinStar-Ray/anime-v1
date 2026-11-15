from __future__ import annotations

from typing import Any

from anime_fortress.errors import SourceError
from anime_fortress.net import headers_from_env, sanitize_url, validate_http_url
from anime_fortress.sources.base import (
    DiscoveryResult,
    EpisodeCandidate,
    MediaLocation,
    SourceAdapter,
    SourceContext,
    SourceRuntime,
    ValidationResult,
    bool_value,
    extract_quality_hint,
    optional_str,
    positive_int,
    require_str,
    stable_item_id,
)


class DirectUrlAdapter(SourceAdapter):
    name = "direct_url"
    description = "Authorized direct media URLs or a JSON list of per-episode URLs"
    config_example = {
        "url": "https://media.example.com/anime/ep01.mkv",
        "season": 1,
        "episode": 1,
        "headers_env": "ANIME_SOURCE_HEADERS",
        "verify_remote": True,
    }

    async def validate(
        self, config: dict[str, Any], runtime: SourceRuntime
    ) -> ValidationResult:
        items = self._items(config)
        headers_env = optional_str(items[0], "headers_env")
        headers = headers_from_env(headers_env, environ=runtime.environ)
        verify_remote = bool_value(items[0], "verify_remote", True)
        checked = 0
        for item in items:
            url = require_str(item, "url")
            await validate_http_url(
                url, allow_private=runtime.settings.allow_private_networks
            )
            positive_int(item, "episode")
            positive_int(item, "season", 1)
            if verify_remote:
                response = await runtime.http.request(
                    url,
                    method="HEAD",
                    headers=headers,
                    raise_for_status=False,
                )
                await response.aclose()
                if response.status_code in {401, 403}:
                    raise SourceError(
                        f"Source denied access to {sanitize_url(url)} "
                        f"(HTTP {response.status_code}). Check headers_env credentials."
                    )
                if response.status_code >= 400 and response.status_code != 405:
                    raise SourceError(
                        f"Media URL is not retrievable: HTTP {response.status_code} "
                        f"for {sanitize_url(url)}"
                    )
                checked += 1
        return ValidationResult(
            ok=True,
            message=(
                f"Direct URL source is valid ({len(items)} media item(s)"
                + (f", {checked} endpoint(s) probed" if checked else "")
                + ")."
            ),
            auth_required=bool(headers_env),
            direct_download=True,
            episodes_found=len({int(item.get("episode", 0)) for item in items}),
            details={"items": len(items), "headers_env": headers_env},
        )

    async def discover(
        self, config: dict[str, Any], context: SourceContext, runtime: SourceRuntime
    ) -> DiscoveryResult:
        items = self._items(config)
        grouped: dict[tuple[int, int], dict[str, Any]] = {}
        warnings: list[str] = []
        for item in items:
            url = require_str(item, "url")
            await validate_http_url(
                url, allow_private=runtime.settings.allow_private_networks
            )
            season = positive_int(item, "season", 1)
            episode = positive_int(item, "episode")
            title = optional_str(item, "title")
            headers_env = optional_str(item, "headers_env")
            quality_hint = optional_str(item, "quality_hint") or extract_quality_hint(title)
            container = optional_str(item, "container")
            audio_hints = item.get("audio_languages") or []
            if isinstance(audio_hints, str):
                audio_hints = [part.strip() for part in audio_hints.split(",") if part.strip()]
            if not isinstance(audio_hints, list):
                raise SourceError("audio_languages must be a list or comma-separated string")
            location = MediaLocation(
                item_id=str(item.get("item_id") or stable_item_id(url, "url")),
                url=url,
                headers_env=headers_env,
                quality_hint=quality_hint,
                container_hint=container,
                size_hint=int(item["size_bytes"]) if item.get("size_bytes") else None,
                audio_languages_hint=tuple(str(value) for value in audio_hints),
                title_hint=title,
            )
            key = (season, episode)
            if key not in grouped:
                grouped[key] = {"title": title, "media": []}
            grouped[key]["media"].append(location)
        episodes = [
            EpisodeCandidate(
                season=season,
                number=number,
                title=entry["title"],
                media=tuple(entry["media"]),
            )
            for (season, number), entry in sorted(grouped.items())
        ]
        if not episodes:
            raise SourceError("Source configuration did not contain any media items")
        if context.anime_title:
            warnings.append(f"Using administrator-supplied direct URLs for {context.anime_title}")
        return DiscoveryResult(episodes=episodes, warnings=warnings)

    def _items(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        raw_items = config.get("items")
        if raw_items is not None:
            if not isinstance(raw_items, list) or not raw_items:
                raise SourceError("'items' must be a non-empty list of media objects")
            items: list[dict[str, Any]] = []
            for entry in raw_items:
                if not isinstance(entry, dict):
                    raise SourceError("Every entry in 'items' must be an object")
                items.append(entry)
        else:
            items = [config]
        merged: list[dict[str, Any]] = []
        default_headers = optional_str(config, "headers_env")
        for item in items:
            copied = dict(item)
            if "headers_env" not in copied and default_headers:
                copied["headers_env"] = default_headers
            merged.append(copied)
        seen: set[tuple[int, int, str]] = set()
        for item in merged:
            url = require_str(item, "url")
            episode = positive_int(item, "episode")
            season = positive_int(item, "season", 1)
            key = (season, episode, url)
            if key in seen:
                raise SourceError(f"Duplicate media URL configured for S{season} E{episode}")
            seen.add(key)
        return merged
