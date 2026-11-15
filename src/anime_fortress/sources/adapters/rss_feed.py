from __future__ import annotations

import re
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from anime_fortress.errors import SourceError
from anime_fortress.net import headers_from_env, validate_http_url
from anime_fortress.sources.base import (
    DiscoveryResult,
    EpisodeCandidate,
    MediaLocation,
    SourceAdapter,
    SourceContext,
    SourceRuntime,
    ValidationResult,
    extract_quality_hint,
    optional_str,
    positive_int,
    require_str,
)

_DEFAULT_SEASON_REGEX = r"(?i)\bS(?P<season>\d{1,2})[ ._-]*E(?:P|p|pisodes?)?[ ._-]*(?P<episode>\d{1,4})\b"
_DEFAULT_EPISODE_REGEX = r"(?i)(?:E|Ep|Episode)[ ._-]*(?P<episode>\d{1,4})\b"
_MEDIA_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".ts", ".webm", ".avi", ".mov"}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _text(element: Any, *names: str) -> str | None:
    for child in element.iter():
        if _local_name(child.tag) in names and child.text and child.text.strip():
            return child.text.strip()
    return None


def _find_media_url(entry: Any) -> tuple[str | None, str | None]:
    fallback: tuple[str | None, str | None] = (None, None)
    for child in entry.iter():
        local = _local_name(child.tag)
        attributes = child.attrib
        if local == "enclosure" and attributes.get("url"):
            media_type = (attributes.get("type") or "").lower()
            suffix = PurePosixPath(urlparse(attributes["url"]).path).suffix.lower()
            if media_type.startswith("video/") or suffix in _MEDIA_EXTENSIONS:
                return attributes["url"], suffix.lstrip(".") or None
            fallback = fallback or (attributes["url"], suffix.lstrip(".") or None)
        if local == "content" and attributes.get("url"):
            suffix = PurePosixPath(urlparse(attributes["url"]).path).suffix.lower()
            if suffix in _MEDIA_EXTENSIONS:
                return attributes["url"], suffix.lstrip(".") or None
            fallback = fallback or (attributes["url"], suffix.lstrip(".") or None)
    if fallback[0]:
        return fallback
    link = _text(entry, "link")
    if link:
        suffix = PurePosixPath(urlparse(link).path).suffix.lower()
        if suffix in _MEDIA_EXTENSIONS:
            return link, suffix.lstrip(".")
    return None, None


class RssFeedAdapter(SourceAdapter):
    name = "rss_feed"
    description = "RSS 2.0 or Atom feed whose entries expose authorized media enclosures"
    config_example = {
        "feed_url": "https://authorized.example.com/feed.xml",
        "default_season": 1,
        "episode_regex": _DEFAULT_EPISODE_REGEX,
        "season_regex": _DEFAULT_SEASON_REGEX,
        "headers_env": None,
    }

    async def _fetch(
        self, config: dict[str, Any], runtime: SourceRuntime
    ) -> tuple[Any, dict[str, str]]:
        feed_url = require_str(config, "feed_url")
        await validate_http_url(
            feed_url, allow_private=runtime.settings.allow_private_networks
        )
        headers_env = optional_str(config, "headers_env")
        headers = headers_from_env(headers_env, environ=runtime.environ)
        body, final_url = await runtime.http.get_bytes(
            feed_url, headers=headers, max_bytes=int(config.get("max_feed_bytes", 5_000_000))
        )
        try:
            root = ElementTree.fromstring(body)
        except (ElementTree.ParseError, DefusedXmlException) as exc:
            raise SourceError(f"Feed is not valid safe XML: {exc}") from exc
        return root, headers

    def _entries(self, root: Any) -> list[Any]:
        local = _local_name(root.tag)
        if local == "rss":
            channel = next((child for child in root if _local_name(child.tag) == "channel"), None)
            if channel is None:
                raise SourceError("RSS feed has no channel element")
            return [child for child in channel if _local_name(child.tag) == "item"]
        if local == "feed":
            return [child for child in root if _local_name(child.tag) == "entry"]
        raise SourceError("Unsupported feed format (expected RSS 2.0 or Atom)")

    def _regexes(self, config: dict[str, Any]) -> tuple[re.Pattern[str], re.Pattern[str]]:
        try:
            season_pattern = re.compile(optional_str(config, "season_regex") or _DEFAULT_SEASON_REGEX)
            episode_pattern = re.compile(
                optional_str(config, "episode_regex") or _DEFAULT_EPISODE_REGEX
            )
        except re.error as exc:
            raise SourceError(f"Invalid feed regex: {exc}") from exc
        if "episode" not in season_pattern.groupindex and season_pattern.groups < 2:
            raise SourceError("season_regex must define season and episode groups")
        if "episode" not in episode_pattern.groupindex and episode_pattern.groups < 1:
            raise SourceError("episode_regex must define an episode group")
        return season_pattern, episode_pattern

    def _parse_episode(
        self,
        title: str,
        *,
        season_pattern: re.Pattern[str],
        episode_pattern: re.Pattern[str],
        default_season: int,
    ) -> tuple[int, int] | None:
        match = season_pattern.search(title)
        if match:
            groups = match.groupdict()
            try:
                if groups.get("episode"):
                    return int(groups.get("season") or default_season), int(groups["episode"])
                if match.lastindex and match.lastindex >= 2:
                    return int(match.group(1) or default_season), int(match.group(2))
            except (TypeError, ValueError):
                return None
        match = episode_pattern.search(title)
        if match:
            groups = match.groupdict()
            try:
                episode = groups.get("episode") or (match.group(match.lastindex) if match.lastindex else None)
                if episode is not None:
                    return default_season, int(episode)
            except (TypeError, ValueError):
                return None
        return None

    async def validate(
        self, config: dict[str, Any], runtime: SourceRuntime
    ) -> ValidationResult:
        root, _headers = await self._fetch(config, runtime)
        entries = self._entries(root)
        if not entries:
            raise SourceError("Feed contains no entries")
        default_season = positive_int(config, "default_season", 1)
        season_pattern, episode_pattern = self._regexes(config)
        parsed = 0
        for entry in entries:
            title = _text(entry, "title")
            if title and self._parse_episode(
                title,
                season_pattern=season_pattern,
                episode_pattern=episode_pattern,
                default_season=default_season,
            ):
                parsed += 1
        if parsed == 0:
            return ValidationResult(
                ok=False,
                message=(
                    f"Feed is reachable with {len(entries)} entries, but no titles matched "
                    "the configured episode regexes."
                ),
                direct_download=True,
            )
        return ValidationResult(
            ok=True,
            message=f"RSS feed valid: {parsed} episode entr{'y' if parsed == 1 else 'ies'} detected.",
            auth_required=bool(optional_str(config, "headers_env")),
            direct_download=True,
            episodes_found=parsed,
        )

    async def discover(
        self, config: dict[str, Any], context: SourceContext, runtime: SourceRuntime
    ) -> DiscoveryResult:
        root, headers = await self._fetch(config, runtime)
        entries = self._entries(root)
        default_season = positive_int(config, "default_season", 1)
        season_pattern, episode_pattern = self._regexes(config)
        headers_env = optional_str(config, "headers_env")
        grouped: dict[tuple[int, int], list[MediaLocation]] = defaultdict(list)
        titles: dict[tuple[int, int], str | None] = {}
        warnings: list[str] = []
        seen_urls: set[str] = set()
        for entry in entries:
            title = _text(entry, "title")
            if not title:
                warnings.append("Skipped feed entry without a title")
                continue
            key = self._parse_episode(
                title,
                season_pattern=season_pattern,
                episode_pattern=episode_pattern,
                default_season=default_season,
            )
            if key is None:
                warnings.append(f"Skipped feed entry with unrecognized episode title: {title[:80]}")
                continue
            media_url, container = _find_media_url(entry)
            if not media_url:
                warnings.append(f"Skipped feed entry without a media enclosure: {title[:80]}")
                continue
            if media_url in seen_urls:
                continue
            seen_urls.add(media_url)
            await validate_http_url(
                media_url, allow_private=runtime.settings.allow_private_networks
            )
            guid = _text(entry, "id", "guid") or media_url
            grouped[key].append(
                MediaLocation(
                    item_id=guid,
                    url=media_url,
                    headers_env=headers_env,
                    quality_hint=extract_quality_hint(title) or extract_quality_hint(media_url),
                    container_hint=container,
                    title_hint=title,
                )
            )
            titles.setdefault(key, title)
        episodes = [
            EpisodeCandidate(
                season=season,
                number=episode,
                title=titles.get((season, episode)),
                media=tuple(locations),
            )
            for (season, episode), locations in sorted(grouped.items())
        ]
        if not episodes:
            raise SourceError("No downloadable episodes could be parsed from the feed")
        return DiscoveryResult(episodes=episodes, warnings=warnings)
