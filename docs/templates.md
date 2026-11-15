# Templates

Two template kinds exist:

- **announcement** — the poster message published once per anime and channel.
- **caption** — the text attached to every uploaded episode document.

Templates use Python `str.format` syntax with a fixed set of placeholders. HTML
markup in the template body is allowed and is sent with Telegram's HTML parse
mode.

## Managing templates

```text
/addtemplate announcement my-announcement
/addtemplate caption my-caption
/showtemplate <id>
/edittemplate <id>
/templates [announcement|caption]
```

The guided flow asks for the template body after the name. A body can also be
given inline:

```text
/addtemplate caption my-caption {file_name}
```

Assignment:

```text
/settemplate <anime_id> <template_id>          # announcement for one anime
/setchanneltemplate <channel_id> <template_id> # caption for one channel
```

If nothing is assigned, the seeded system templates `default-announcement` and
`default-caption` are used. Without a system template, the `ANNOUNCEMENT_TEMPLATE`
and `CAPTION_TEMPLATE` settings are used as the fallback body.

## Placeholders

Announcement templates can use every field below. Caption templates expose the
same names, but episode-specific fields are filled in per file and
catalogue-level fields such as `{seasons}` render as `N/A` in captions.

| Placeholder | Example | Notes |
| --- | --- | --- |
| `{anime_name}` | `Frieren` | Primary title |
| `{anime_title}` | `Frieren` | Alias of `anime_name` |
| `{alternative_titles}` | `Sousou no Frieren` | Comma separated |
| `{japanese_title}` | `葬送のフリーレン` | |
| `{release_date}` | `09 Oct 2023` | |
| `{release_year}` | `2023` | Numeric |
| `{seasons}` | `2` | Number of seasons (announcement only) |
| `{episode_count}` | `28` | |
| `{type}` | `TV` | |
| `{genres}` | `Action, Adventure` | Comma separated |
| `{studio}` | `Madhouse` | |
| `{rating}` | `9.0` | Pre-formatted with one decimal |
| `{rating_source}` | `AniList` | |
| `{status}` | `Finished` | |
| `{duration}` | `24 min` | |
| `{age_rating}` | `PG-13` | |
| `{synopsis}` | `After being orphaned...` | |
| `{broadcast}` | `Thursdays, 23:00` | |
| `{audio_languages}` | `Hindi, English, Japanese` | From processed files |
| `{available_qualities}` | `480p, 720p, 1080p` | From processed files |
| `{branding}` | `@YourChannel` | Channel branding or the default |
| `{channel_name}` | `Anime Channel` | |
| `{file_name}` | `S01E01 ... .mkv` | Caption field |
| `{quality}` | `1080p` | Caption field |
| `{audio_label}` | `Dual` | Caption field |
| `{season}` | `1` | Caption field, numeric |
| `{episode}` | `5` | Caption field, numeric |
| `{year}` | `2023` | Caption field, numeric |

Unknown placeholders are rejected when the template is saved, so typos fail
fast instead of rendering `N/A`.

## Rendering rules

- Missing values render as `MISSING_VALUE_TEXT` (default `N/A`).
- Lists are joined with `, `.
- Booleans render as `Yes`/`No`.
- Interpolated values are HTML-escaped, so metadata containing `<` or `&` is
  safe. Write markup directly in the template body.
- Trailing whitespace is removed and runs of blank lines are collapsed to a
  single blank line when `CLEANUP_BLANK_LINES` is enabled.
- Numeric fields (`season`, `episode`, `release_year`, `episode_count`,
  `seasons`, `year`) accept format specifications such as `{season:02d}`.
- A caption longer than `MAX_CAPTION_LENGTH` (default 1024) fails the task
  with a clear error instead of being silently truncated.
- An announcement longer than `MAX_MESSAGE_LENGTH` (default 4096) is converted
  to plain text and split into numbered parts.

## Examples

Default announcement:

```text
<b>{anime_name}</b>

╭──────────────────────
‣ SEASONS : {seasons}
‣ TYPE : {type}
‣ AUDIO : {audio_languages}
‣ QUALITY : {available_qualities}
‣ GENRE : {genres}
‣ AVERAGE RATING : {rating}
‣ STATUS : {status}
╰──────────────────────

<blockquote>‣ <b>Synopsis</b> :
{synopsis}</blockquote>
```

Caption with the file name and quality:

```text
{file_name}
```

Caption with episode information:

```text
<b>{anime_title}</b> — S{season:02d}E{episode:02d} [{quality}] [{audio_label}]
{file_name}
```

Caption with the channel branding:

```text
{file_name}
{branding}
```

## Filename pattern

The filename is built from `FILENAME_PATTERN`, which uses the same placeholder
syntax but a different set of fields:

```text
[S{season:02d}-Ep{episode:02d}] {anime_title} [{quality}] [{audio_label}] {branding}.mkv
```

Available fields: `{season}`, `{episode}`, `{anime_title}`, `{year}`,
`{anime_type}`, `{quality}`, `{audio_label}`, `{branding}`. The result is
truncated to `MAX_DOCUMENT_FILENAME_LENGTH` (default 120) characters.

`{audio_label}` is built from the audio tracks that were actually muxed:

| Tracks | Label (default style `multi`) |
| --- | --- |
| no audio | `No Audio` |
| 1 language | the language name |
| 2 languages | `Dual` (`DUAL_AUDIO_LABEL`) |
| 3 or more | `Multi Audio` (`MULTI_AUDIO_LABEL`) |

With `AUDIO_LABEL_STYLE=joined` the short language codes are joined with `-`
instead (`Hin-Eng`).

## Testing a template

Use `/preview <anime_id>` to render the announcement with live data and see
the warnings (missing poster, no processed qualities, oversized text) before
publishing anything.
