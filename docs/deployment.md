# Deployment

## Supported topologies

| Topology | When to use |
| --- | --- |
| Bot + worker in one process (`anime-fortress bot`) | Single machine, simplest setup |
| Separate bot and worker processes | Worker on a beefier host; both must share the same database |
| Docker image | Reproducible deployment with FFmpeg bundled |

**One worker process per database.** Restart recovery requeues every running
task, so two workers on the same database would fight over the same tasks. Use
`WORKER_CONCURRENCY` for parallelism instead.

## Local install

```bash
git clone https://github.com/RuinStar-Ray/anime-v1.git
cd anime-v1
uv sync --extra dev            # add --extra postgres for PostgreSQL
cp .env.example .env
uv run anime-fortress doctor
uv run anime-fortress init-db
uv run anime-fortress bot
```

`init-db` applies migrations and seeds the default templates and settings. The
`bot` and `worker` commands apply pending migrations automatically unless
`--no-migrate` is passed.

## Environment

Configuration comes from the environment and `.env`. At minimum set:

```bash
BOT_TOKEN=123456:replace-me
ADMIN_USER_IDS=11111111,22222222
DEFAULT_BRANDING=@YourChannel
```

Full list: see `.env.example` and the README.

## Data directories

| Directory | Contents |
| --- | --- |
| `DATA_DIR` | Root for all runtime data (default `./data`) |
| `DATA_DIR/media` | Rendered MKV files, one folder per anime and season |
| `DATA_DIR/posters` | Uploaded posters |
| `DATA_DIR/thumbnails` | Generated upload thumbnails |
| `DATA_DIR/tmp` | Download and remux scratch space (cleaned up automatically) |
| `DATA_DIR/anime.db` | SQLite database (when `DATABASE_URL` is empty) |

Processed media is kept after upload so that re-uploads and verification are
possible. Back it up together with the database, or delete old seasons
manually when disk space matters.

## PostgreSQL

```bash
uv sync --extra postgres
export DATABASE_URL='postgresql+asyncpg://anime:password@127.0.0.1:5432/anime'
uv run anime-fortress migrate
```

Notes:

- Use the `postgresql+asyncpg` driver string.
- The `postgres` extra must be installed in every environment that runs the
  bot, the worker, or migrations.
- Migrations run with `compare_type=True`, so keep the PostgreSQL major version
  stable across deployments.
- Back up with `pg_dump`; the SQLite file is not involved.

## systemd

`/etc/systemd/system/anime-fortress-bot.service`:

```ini
[Unit]
Description=Anime Fortress admin bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=anime
WorkingDirectory=/opt/anime-v1
EnvironmentFile=/opt/anime-v1/.env
ExecStart=/opt/anime-v1/.venv/bin/anime-fortress bot
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/anime-fortress-worker.service`:

```ini
[Unit]
Description=Anime Fortress worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=anime
WorkingDirectory=/opt/anime-v1
EnvironmentFile=/opt/anime-v1/.env
ExecStart=/opt/anime-v1/.venv/bin/anime-fortress worker
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable both:

```bash
sudo systemctl enable --now anime-fortress-bot anime-fortress-worker
sudo journalctl -u anime-fortress-worker -f
```

`SIGTERM` is handled: the worker stops claiming new tasks, waits up to 20
seconds for running tasks, and requeues anything that was still running.

## Docker

Build:

```bash
docker build -t anime-fortress .
```

Run the bot with a worker inside the container:

```bash
docker run --rm -it \
  --env-file .env \
  -v "$PWD/data:/app/data" \
  anime-fortress
```

Run a dedicated worker container against the same database and the same media
volume:

```bash
docker run --rm -it \
  --env-file .env \
  -v "$PWD/data:/app/data" \
  anime-fortress anime-fortress worker
```

Compose:

```yaml
services:
  bot:
    build: .
    env_file: .env
    volumes:
      - ./data:/app/data
    restart: unless-stopped

  worker:
    build: .
    command: ["anime-fortress", "worker"]
    env_file: .env
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

The image already contains FFmpeg and runs as a non-root user. `DATA_DIR`
defaults to `/app/data` inside the container.

## Telegram setup

1. Create the bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Add the bot to the destination channel as an administrator with permission to
   post messages (and to edit the channel's own posts if you want to fix
   captions later).
3. Send `/id` to the bot and add the returned user ID to `ADMIN_USER_IDS`.
4. Register the channel in Anime Fortress with `/addchannel @channel`.

## Upgrades

```bash
git pull
uv sync --extra dev
uv run anime-fortress migrate
sudo systemctl restart anime-fortress-bot anime-fortress-worker
```

Migrations are forward-only. Take a database backup before upgrading.

## Monitoring

- `anime-fortress doctor` validates the environment and the schema.
- `/status` shows entity counts, episode statuses, queue counts, and the worker
  heartbeat.
- `/queue` shows the most recent tasks with their status and stage.
- `upload_records` keeps the full upload history with error messages.

## Backups

Back up at least:

- the database (`data/anime.db` or a `pg_dump`),
- the `.env` file (store it in a secret manager, not in the repository),
- the posters directory (small, and needed to republish announcements).

Processed media can be regenerated from the sources, so it is optional — but
regenerating it costs bandwidth and processing time.
