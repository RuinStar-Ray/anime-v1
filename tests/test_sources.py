from __future__ import annotations

from pathlib import Path

import pytest

from anime_fortress.config import Settings
from anime_fortress.errors import SourceError
from anime_fortress.sources.adapters.direct_url import DirectUrlAdapter
from anime_fortress.sources.adapters.local_library import LocalLibraryAdapter
from anime_fortress.sources.adapters.rss_feed import RssFeedAdapter
from anime_fortress.sources.base import (
    MediaLocation,
    SourceContext,
    SourceRuntime,
    extract_quality_hint,
)
from anime_fortress.sources.registry import default_source_registry


class FakeHttp:
    def __init__(self, payload: bytes = b"") -> None:
        self.payload = payload
        self.urls: list[str] = []

    async def get_bytes(self, url: str, *, headers=None, max_bytes: int = 0):
        self.urls.append(url)
        return self.payload, url


def _runtime(tmp_path: Path, *, http=None, roots: list[Path] | None = None) -> SourceRuntime:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        media_library_roots=roots or [],
        allow_private_networks=True,
    )
    return SourceRuntime(
        settings=settings,
        http=http or FakeHttp(),  # type: ignore[arg-type]
        environ={},
        allowed_library_roots=roots or settings.media_library_roots,
    )


def test_media_location_requires_exactly_one_source() -> None:
    with pytest.raises(ValueError):
        MediaLocation(item_id="x")
    with pytest.raises(ValueError):
        MediaLocation(item_id="x", url="https://x", local_path="file.mkv")
    assert MediaLocation(item_id="x", url="https://x").url == "https://x"


def test_extract_quality_hint() -> None:
    assert extract_quality_hint("Show.S01E01.1080p.mkv") == "1080p"
    assert extract_quality_hint("Show 720P") == "720p"
    assert extract_quality_hint("Show") is None


def test_registry_contains_builtins_and_rejects_unknown() -> None:
    registry = default_source_registry(load_plugins=False)
    assert registry.names() == ["direct_url", "local_library", "rss_feed"]
    with pytest.raises(SourceError):
        registry.get("missing")


async def test_local_library_discovery_and_allowlist(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    (root / "Show S01E02 1080p.mkv").write_bytes(b"two")
    (root / "Show S01E01 720p.mkv").write_bytes(b"one")
    (root / "Show S01E01 720p.srt").write_bytes(b"sub")
    (root / "not-an-episode.mkv").write_bytes(b"noop")
    runtime = _runtime(tmp_path, roots=[root])
    adapter = LocalLibraryAdapter()

    result = await adapter.validate({"root": str(root)}, runtime)
    assert result.ok is True
    assert result.episodes_found == 2
    assert result.direct_download is False

    discovery = await adapter.discover(
        {"root": str(root), "recursive": True},
        SourceContext(anime_title="Show"),
        runtime,
    )
    assert [(item.season, item.number) for item in discovery.episodes] == [(1, 1), (1, 2)]
    first = discovery.episodes[0]
    assert first.media[0].quality_hint == "720p"
    assert first.media[0].container_hint == "mkv"
    assert first.media[0].local_path is not None
    assert any("without an episode number" in warning for warning in discovery.warnings)
    assert any("outside the source root" in warning for warning in discovery.warnings) is False


async def test_local_library_rejects_root_outside_allowlist(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Show S01E01.mkv").write_bytes(b"x")
    runtime = _runtime(tmp_path, roots=[allowed])
    with pytest.raises(SourceError, match="outside"):
        await LocalLibraryAdapter().validate({"root": str(outside)}, runtime)


async def test_local_library_invalid_regex(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    runtime = _runtime(tmp_path, roots=[root])
    with pytest.raises(SourceError, match="episode_regex"):
        await LocalLibraryAdapter().validate(
            {"root": str(root), "episode_regex": ".*"}, runtime
        )


RSS_XML = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Feed</title>
  <item>
    <title>Show S01E01 1080p</title>
    <guid>guid-1</guid>
    <enclosure url="https://media.example.com/ep1.mkv" type="video/x-matroska"/>
  </item>
  <item>
    <title>Show S01E02 720p</title>
    <guid>guid-2</guid>
    <enclosure url="https://media.example.com/ep2.mp4" type="video/mp4"/>
  </item>
  <item><title>Not an episode</title></item>
</channel></rss>"""


async def test_rss_discovery(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def no_validate(url: str, *, allow_private: bool = False) -> str:
        return url

    monkeypatch.setattr(
        "anime_fortress.sources.adapters.rss_feed.validate_http_url", no_validate
    )
    http = FakeHttp(RSS_XML)
    runtime = _runtime(tmp_path, http=http)
    adapter = RssFeedAdapter()
    validation = await adapter.validate(
        {"feed_url": "https://example.com/feed.xml"}, runtime
    )
    assert validation.ok is True
    assert validation.episodes_found == 2
    discovery = await adapter.discover(
        {"feed_url": "https://example.com/feed.xml"},
        SourceContext(anime_title="Show"),
        runtime,
    )
    assert [(item.season, item.number) for item in discovery.episodes] == [(1, 1), (1, 2)]
    assert discovery.episodes[0].media[0].quality_hint == "1080p"
    assert discovery.episodes[0].media[0].container_hint == "mkv"
    assert any("unrecognized" in warning for warning in discovery.warnings)


async def test_rss_rejects_malformed_and_unsafe_xml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def no_validate(url: str, *, allow_private: bool = False) -> str:
        return url

    monkeypatch.setattr(
        "anime_fortress.sources.adapters.rss_feed.validate_http_url", no_validate
    )
    runtime = _runtime(tmp_path, http=FakeHttp(b"<rss><channel>"))
    with pytest.raises(SourceError, match="valid safe XML"):
        await RssFeedAdapter().validate(
            {"feed_url": "https://example.com/feed.xml"}, runtime
        )

    evil = b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><rss><channel><item><title>&xxe;</title></item></channel></rss>'
    runtime = _runtime(tmp_path, http=FakeHttp(evil))
    with pytest.raises(SourceError):
        await RssFeedAdapter().validate(
            {"feed_url": "https://example.com/feed.xml"}, runtime
        )


async def test_direct_url_config_validation_and_discovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def no_validate(url: str, *, allow_private: bool = False) -> str:
        return url

    class HeadResponse:
        status_code = 200

        async def aclose(self) -> None:
            return None

    class HeadHttp(FakeHttp):
        async def request(self, url: str, **kwargs):
            return HeadResponse()

    monkeypatch.setattr(
        "anime_fortress.sources.adapters.direct_url.validate_http_url", no_validate
    )
    runtime = _runtime(tmp_path, http=HeadHttp())
    config = {
        "items": [
            {
                "url": "https://media.example.com/show/ep1.mkv",
                "season": 1,
                "episode": 1,
                "title": "Show S01E01 1080p",
                "audio_languages": ["Hindi", "English"],
            },
            {
                "url": "https://media.example.com/show/ep1-720.mp4",
                "season": 1,
                "episode": 1,
                "title": "Show S01E01 720p",
            },
        ]
    }
    validation = await DirectUrlAdapter().validate(config, runtime)
    assert validation.ok is True
    assert validation.episodes_found == 1
    discovery = await DirectUrlAdapter().discover(
        config, SourceContext(anime_title="Show"), runtime
    )
    assert len(discovery.episodes) == 1
    assert len(discovery.episodes[0].media) == 2
    assert discovery.episodes[0].media[0].audio_languages_hint == ("Hindi", "English")
    assert discovery.episodes[0].media[1].quality_hint == "720p"


async def test_direct_url_rejects_duplicates_and_missing_fields(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    with pytest.raises(SourceError, match="Duplicate"):
        await DirectUrlAdapter().validate(
            {
                "items": [
                    {"url": "https://example.com/a.mkv", "episode": 1},
                    {"url": "https://example.com/a.mkv", "episode": 1},
                ]
            },
            runtime,
        )
    with pytest.raises(SourceError, match="episode"):
        await DirectUrlAdapter().validate(
            {"items": [{"url": "https://example.com/a.mkv"}]}, runtime
        )
