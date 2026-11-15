# Sources

A *source* tells Anime Fortress where episode media comes from. Each source row
stores a name, an adapter name, and a JSON configuration. Sources are managed
from the bot (`/addsource`, `/listsources`, `/checksource`) and are attached to
an anime with `/setsource`.

Only configure sources you are authorized to access. Anime Fortress refuses
non-HTTP(S) URLs, blocks requests to private network ranges by default, and
never stores credentials inside the source configuration.

## Built-in adapters

| Adapter | Purpose |
| --- | --- |
| `direct_url` | One or more authorized direct media URLs mapped to episodes |
| `local_library` | A local media directory, restricted to `MEDIA_LIBRARY_ROOTS` |
| `rss_feed` | An RSS 2.0 or Atom feed whose entries expose media enclosures |

Register a source with the guided flow:

```text
/addsource
```

The bot lists the installed adapters, shows an example configuration for the
chosen adapter, and stores the JSON you send. You can also register one
without the guided flow:

```text
/addsource MySource direct_url {"url":"https://media.example.com/ep01.mkv","season":1,"episode":1}
```

`/checksource <source_id>` re-runs validation and stores the result in the
source's `status` and `last_error` columns.

### direct_url

Single media item:

```json
{
  "url": "https://media.example.com/anime/ep01.mkv",
  "season": 1,
  "episode": 1,
  "title": "Episode 1",
  "quality_hint": "1080p",
  "container": "mkv",
  "audio_languages": ["Hindi", "English"],
  "size_bytes": 1400000000,
  "headers_env": "ANIME_SOURCE_HEADERS",
  "verify_remote": true
}
```

Multiple episodes in one source:

```json
{
  "items": [
    {"url": "https://media.example.com/ep01.mkv", "season": 1, "episode": 1},
    {"url": "https://media.example.com/ep02.mkv", "season": 1, "episode": 2}
  ],
  "headers_env": "ANIME_SOURCE_HEADERS"
}
```

Fields:

| Field | Required | Meaning |
| --- | --- | --- |
| `url` | yes | Absolute HTTP(S) media URL |
| `season` | no (default 1) | Season number, positive integer |
| `episode` | yes | Episode number, positive integer |
| `title` | no | Episode title shown in the catalogue |
| `quality_hint` | no | Quality label such as `1080p`; guessed from the title if absent |
| `container` | no | Container hint (`mkv`, `mp4`, ...) |
| `audio_languages` | no | List or comma-separated string of expected languages |
| `size_bytes` | no | Expected size, used for planning only |
| `headers_env` | no | Name of an environment variable holding request headers as JSON |
| `verify_remote` | no | Probe the URL with `HEAD` during validation (default `true`) |
| `item_id` | no | Stable identifier; derived from the URL when absent |

Duplicate `(season, episode, url)` combinations are rejected during validation.

### local_library

```json
{
  "root": "D:/AuthorizedAnime/Show Name",
  "season": 1,
  "recursive": true,
  "extensions": [".mkv", ".mp4", ".m4v", ".ts", ".webm"],
  "episode_regex": "(?i)(?:S(?P<season>\\d{1,2}))?[^0-9]*(?:E|Ep|Episode)[ ._-]*(?P<episode>\\d{1,4})",
  "max_files": 5000
}
```

- `root` must resolve inside one of the `MEDIA_LIBRARY_ROOTS` entries, otherwise
  the adapter refuses the source.
- The directory is scanned recursively by default; the regex must expose an
  `episode` group (a `season` group is optional).
- Files that do not match the regex are reported as discovery warnings and
  skipped.
- The default extensions are `.mkv`, `.mp4`, `.m4v`, `.ts`, `.webm`.

### rss_feed

```json
{
  "feed_url": "https://authorized.example.com/feed.xml",
  "default_season": 1,
  "season_regex": "(?i)\\bS(?P<season>\\d{1,2})[ ._-]*E(?:P|p|pisodes?)?[ ._-]*(?P<episode>\\d{1,4})\\b",
  "episode_regex": "(?i)(?:E|Ep|Episode)[ ._-]*(?P<episode>\\d{1,4})\\b",
  "headers_env": "ANIME_SOURCE_HEADERS",
  "max_feed_bytes": 5000000
}
```

- RSS 2.0 and Atom are both supported.
- Entries are matched by title using the season and episode regexes.
- The media URL is taken from `enclosure`, `content`, or `link` elements whose
  type or extension looks like video.
- Feed XML is parsed with `defusedxml`, so entity expansion attacks are
  rejected.
- Entries without a title, without a recognizable episode number, or without a
  media enclosure are skipped with a warning.

## Authentication headers

Keep credentials out of the database. Store them in an environment variable
and reference the variable name with `headers_env`:

```bash
ANIME_SOURCE_HEADERS='{"Authorization":"Bearer <token>"}'
```

The value must be a JSON object of HTTP headers. It is applied to every
request for that source (feed fetch, media download, and validation probe).

## Network safety

- Only `http://` and `https://` URLs are accepted.
- Private, loopback, and link-local addresses are rejected unless
  `ALLOW_PRIVATE_NETWORKS=true`. Enable it only for sources on networks you
  control.
- Downloads are capped by `MAX_DOWNLOAD_MB`; feed bodies by `max_feed_bytes`.
- Every request sends a configurable `USER_AGENT`.

## Custom adapters

Adapters subclass `SourceAdapter` and implement two coroutines:

```python
from anime_fortress.sources.base import (
    DiscoveryResult, SourceAdapter, SourceRuntime, ValidationResult,
)

class MyAdapter(SourceAdapter):
    name = "my_adapter"
    description = "What this adapter does"
    config_example = {"url": "https://example.invalid/feed"}

    async def validate(self, config, runtime: SourceRuntime) -> ValidationResult:
        return ValidationResult(ok=True, message="ok", episodes_found=0)

    async def discover(self, config, context, runtime: SourceRuntime) -> DiscoveryResult:
        return DiscoveryResult(episodes=[], warnings=[])
```

Register it through the `anime_fortress.sources` entry-point group:

```toml
[project.entry-points."anime_fortress.sources"]
my_adapter = "my_package.adapter:MyAdapter"
```

The entry point must resolve to a zero-argument factory returning a
`SourceAdapter` instance. Plugins are loaded at startup; a broken plugin is
logged and skipped instead of stopping the application.

## Discovery flow

1. `/process <anime_id>` calls the adapter's `discover()` with the anime title
   as context.
2. Seasons, episodes, and `source_media` rows are created or updated.
3. A `PROCESS_EPISODE` task is queued for every episode that is not already
   uploaded.
4. The announcement task is queued during discovery when
   `REQUIRE_ANNOUNCEMENT` is enabled, and is deduplicated per anime and
   channel.

Re-running `/process` is safe: known episodes are updated, finished episodes are
skipped, and the queue deduplicates work that is still pending.
