from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from anime_fortress.errors import SourceError
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
)

_DEFAULT_EPISODE_REGEX = r"(?i)(?:S(?P<season>\d{1,2}))?[^0-9]*(?:E|Ep|Episode)[ ._-]*(?P<episode>\d{1,4})"
_DEFAULT_EXTENSIONS = (".mkv", ".mp4", ".m4v", ".ts", ".webm")


class LocalLibraryAdapter(SourceAdapter):
    name = "local_library"
    description = "Authorized local media library directory (must be under MEDIA_LIBRARY_ROOTS)"
    config_example = {
        "root": "D:/AuthorizedAnime/Show Name",
        "season": 1,
        "recursive": True,
        "episode_regex": _DEFAULT_EPISODE_REGEX,
    }

    def _root(self, config: dict[str, Any], runtime: SourceRuntime) -> Path:
        raw_root = require_str(config, "root")
        root = Path(raw_root).expanduser().resolve()
        allowed = runtime.allowed_library_roots or runtime.settings.media_library_roots
        if not allowed:
            raise SourceError(
                "No media library roots are configured. Set MEDIA_LIBRARY_ROOTS in the environment."
            )
        resolved_allowed = [path.expanduser().resolve() for path in allowed]
        if not any(root.is_relative_to(candidate) for candidate in resolved_allowed):
            raise SourceError(
                "Local source root is outside the configured MEDIA_LIBRARY_ROOTS allowlist"
            )
        return root

    def _settings(self, config: dict[str, Any]) -> tuple[re.Pattern[str], tuple[str, ...], int, bool]:
        pattern_text = optional_str(config, "episode_regex") or _DEFAULT_EPISODE_REGEX
        try:
            pattern = re.compile(pattern_text)
        except re.error as exc:
            raise SourceError(f"Invalid episode_regex: {exc}") from exc
        if "episode" not in pattern.groupindex and pattern.groups == 0:
            raise SourceError("episode_regex must contain an 'episode' named group or a capture group")
        extensions_raw = config.get("extensions") or list(_DEFAULT_EXTENSIONS)
        if not isinstance(extensions_raw, list):
            raise SourceError("extensions must be a list")
        extensions = tuple(
            str(ext).lower() if str(ext).startswith(".") else f".{str(ext).lower()}"
            for ext in extensions_raw
        )
        default_season = positive_int(config, "season", 1)
        recursive = bool_value(config, "recursive", True)
        return pattern, extensions, default_season, recursive

    def _parse_episode(
        self, pattern: re.Pattern[str], filename: str, default_season: int
    ) -> tuple[int, int] | None:
        match = pattern.search(filename)
        if not match:
            return None
        groups = match.groupdict()
        try:
            if groups.get("episode"):
                episode = int(groups["episode"])
                season = int(groups.get("season") or default_season)
            elif match.lastindex and match.lastindex >= 2:
                season = int(match.group(1) or default_season)
                episode = int(match.group(2))
            elif match.lastindex == 1:
                episode = int(match.group(1))
                season = default_season
            else:
                return None
        except (TypeError, ValueError):
            return None
        if season <= 0 or episode <= 0:
            return None
        return season, episode

    def _scan(
        self, config: dict[str, Any], runtime: SourceRuntime
    ) -> tuple[list[Path], re.Pattern[str], tuple[str, ...], int]:
        root = self._root(config, runtime)
        if not root.exists():
            raise SourceError(f"Local library root does not exist: {root}")
        if not root.is_dir():
            raise SourceError(f"Local library root is not a directory: {root}")
        pattern, extensions, default_season, recursive = self._settings(config)
        max_files = int(config.get("max_files", 5000))
        iterator = root.rglob("*") if recursive else root.glob("*")
        files: list[Path] = []
        for path in iterator:
            if len(files) >= max_files:
                raise SourceError(f"Local library contains more than max_files={max_files} media files")
            if not path.is_file():
                continue
            if path.name.startswith("."):
                continue
            if path.suffix.lower() not in extensions:
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                continue
            files.append(resolved)
        return sorted(files), pattern, extensions, default_season

    async def validate(
        self, config: dict[str, Any], runtime: SourceRuntime
    ) -> ValidationResult:
        files, pattern, _, default_season = self._scan(config, runtime)
        parsed = [name for name in files if self._parse_episode(pattern, name.name, default_season)]
        if not parsed:
            return ValidationResult(
                ok=False,
                message=(
                    f"Root is allowed and readable, but no files matched episode_regex "
                    f"({len(files)} media file(s) found)."
                ),
                direct_download=False,
            )
        episode_keys = {
            self._parse_episode(pattern, path.name, default_season) for path in parsed
        }
        return ValidationResult(
            ok=True,
            message=(
                f"Local library valid: {len(parsed)} media file(s) across "
                f"{len(episode_keys)} episode(s)."
            ),
            auth_required=False,
            direct_download=False,
            episodes_found=len(episode_keys),
            details={"root": str(self._root(config, runtime)), "files": len(files)},
        )

    async def discover(
        self, config: dict[str, Any], context: SourceContext, runtime: SourceRuntime
    ) -> DiscoveryResult:
        files, pattern, _, default_season = self._scan(config, runtime)
        grouped: dict[tuple[int, int], list[MediaLocation]] = defaultdict(list)
        titles: dict[tuple[int, int], str | None] = {}
        warnings: list[str] = []
        root = self._root(config, runtime)
        for path in files:
            key = self._parse_episode(pattern, path.name, default_season)
            if key is None:
                warnings.append(f"Skipped file without an episode number: {path.name}")
                continue
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                warnings.append(f"Skipped file outside the source root: {path.name}")
                continue
            size = path.stat().st_size
            grouped[key].append(
                MediaLocation(
                    item_id=relative,
                    local_path=str(path),
                    quality_hint=extract_quality_hint(path.name),
                    container_hint=path.suffix.lower().lstrip("."),
                    size_hint=size,
                    title_hint=path.stem,
                )
            )
            titles.setdefault(key, path.stem)
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
            raise SourceError("No episode files matched the configured regex under the source root")
        return DiscoveryResult(episodes=episodes, warnings=warnings)
