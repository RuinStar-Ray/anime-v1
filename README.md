# Anime Fortress

Automated, auditable anime publishing pipeline for Telegram channels.

Anime Fortress discovers authorized episode sources, enriches anime metadata,
generates announcement posters and captions, remuxes every available audio track
into one MKV per quality, and publishes through a persistent queue. It ships with
an admin-only Telegram bot, CLI diagnostics, Alembic migrations, tests, and a
container image.

## Features

- Source adapters for authorized direct URLs, a local media library, and
  RSS/Atom feeds, plus an entry-point plugin interface.
- Jikan and AniList metadata with a guided manual-entry fallback.
- Poster upload and thumbnail generation via FFmpeg.
- Multi-audio MKV remuxing with language, codec, subtitle, and quality handling;
  no upscaling and no fabricated language labels.
- Priority queue with retries, pause/resume, crash recovery, checksum
  deduplication, and strict episode/quality ordering.
- Announcement-before-episodes publishing, with a persistent publish history.
- Admin Telegram bot and CLI (`doctor`, `init-db`, `migrate`, `bot`, `worker`).
- SQLite by default, PostgreSQL supported through the `postgres` extra.

## Requirements

- Python 3.13 (the project pins `>=3.13,<3.14`)
- [uv](https://docs.astral.sh/uv/)
- FFmpeg and FFprobe on `PATH`
- A Telegram bot token and a destination channel where the bot is an
  administrator with post permission
- Authorized access to every media source you configure

## Quick start

```bash
git clone https://github.com/RuinStar-Ray/anime-v1.git
cd anime-v1
uv sync --extra dev
cp .env.example .env
```

Edit `.env` and set at least `BOT_TOKEN` and `ADMIN_USER_IDS`. Then:

```bash
uv run anime-fortress doctor
uv run anime-fortress init-db
uv run anime-fortress bot
```

`bot` starts both the admin bot and the queue worker. To run them separately:

```bash
uv run anime-fortress worker
uv run anime-fortress bot
```

Use exactly one worker process per database. `WORKER_CONCURRENCY` controls
parallel task execution inside that process.

Windows notes:

- Use FFmpeg builds that include `ffmpeg.exe` and `ffprobe.exe`, or set
  `FFMPEG_PATH`/`FFPROBE_PATH` to absolute paths.
- Separate `MEDIA_LIBRARY_ROOTS` entries with `;`.
- Runtime data defaults to `./data`; SQLite lives at `data/anime.db`.

## First-run workflow

1. Get your numeric Telegram ID with `/id` and add it to `ADMIN_USER_IDS`.
2. In the bot, register the destination channel with
   `/addchannel @channel [branding]`. The bot must already be an administrator.
3. Register a source with `/addsource` (or configure one interactively).
4. Add anime with `/addanime`, then assign the source and channel:
   `/setsource`, `/setchannel`.
5. Upload a poster with `/setposter`, set `/setlanguages` and `/setquality`,
   then inspect `/preview`.
6. Run `/process <anime_id>`. Episodes are discovered, processed, announced, and
   uploaded by the worker.
7. Monitor with `/status`, `/queue`, and `/history`. Use `/pause`, `/resume`,
   `/retry`, and `/skip` when needed.

## Commands

Run `uv run anime-fortress --help` for CLI help, or `/help` in the bot for the
full admin command list.

| CLI | Purpose |
| --- | --- |
| `doctor` | Validate directories, database schema, FFmpeg, providers, and config |
| `init-db` | Apply migrations and seed default templates/settings |
| `migrate` | Apply pending Alembic migrations |
| `bot` | Run the admin bot and worker |
| `worker` | Run the queue worker only |

## Configuration

Configuration is environment-driven, with `.env` loaded automatically. See
[`.env.example`](.env.example) for every option and safe defaults. Important
settings:

- `BOT_TOKEN`, `ADMIN_USER_IDS`, `DEFAULT_BRANDING`
- `DATABASE_URL` (empty means SQLite under `DATA_DIR`)
- `MEDIA_LIBRARY_ROOTS` for local sources
- `PREFERRED_LANGUAGES`, `PREFERRED_QUALITIES`, `INCLUDE_OTHER_QUALITIES`
- `STRICT_EPISODE_ORDER`, `REQUIRE_ANNOUNCEMENT`
- `MAX_UPLOAD_MB` (keep below the active Telegram Bot API limit)
- `WORKER_CONCURRENCY`, `TASK_MAX_ATTEMPTS`

PostgreSQL:

```bash
uv sync --extra postgres
export DATABASE_URL='postgresql+asyncpg://anime:password@localhost:5432/anime'
uv run anime-fortress migrate
```

## Docker

```bash
docker build -t anime-fortress .
docker run --rm -it \
  --env-file .env \
  -v "$PWD/data:/app/data" \
  anime-fortress
```

The image includes FFmpeg, runs as a non-root user, and defaults to
`anime-fortress bot`. Set `DATA_DIR=/app/data` when overriding paths. See
[docs/deployment.md](docs/deployment.md) for Compose and systemd examples.

## Testing and quality

```bash
uv run pytest -q
uv run ruff check src tests alembic
```

The default suite covers config, naming, templates, queue behavior, planning,
sources, publishing, status, bot commands, and a real FFmpeg integration path.
Live Telegram publishing and third-party source access require your own
credentials and are not exercised in CI.

## Documentation

- [Architecture](docs/architecture.md)
- [Sources](docs/sources.md)
- [Templates](docs/templates.md)
- [Deployment](docs/deployment.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Security](docs/security.md)

## Project layout

```text
src/anime_fortress/
  bot/          admin bot, guards, handlers, states
  db/           models, enums, repositories, session
  media/        probing, FFmpeg, planning, processing
  metadata/     Jikan/AniList providers and registry
  services/     discovery, presentation, publish, worker, queue helpers
  sources/      adapter base, registry, built-in adapters
  telegram/     sender abstraction and publisher
  app.py        application container
  cli.py        command-line entry point
alembic/        schema migrations
tests/          pytest suite
```

## Legal and operational notice

Only configure sources and content you are authorized to download, process, and
publish. You are responsible for compliance with copyright, platform, and local
law. Keep the bot admin-only, protect the token, and review
[docs/security.md](docs/security.md) before production use.
