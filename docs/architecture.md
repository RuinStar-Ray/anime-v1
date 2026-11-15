# Architecture

## Overview

Anime Fortress is a single Python package (`anime_fortress`) with three
runtime entry points:

- the admin Telegram bot (`anime-fortress bot`),
- the queue worker (`anime-fortress worker`, or embedded in the bot process),
- maintenance commands (`anime-fortress doctor|init-db|migrate`).

The `Application` container (`src/anime_fortress/app.py`) wires every service
to one database session factory, one HTTP client, one FFmpeg wrapper, and one
Telegram sender. Nothing reaches out to the network except through those
objects, which keeps mocking and auditing straightforward.

```text
Telegram admin  ──▶ bot handlers ──▶ services ──┐
                                                ├──▶ repositories ──▶ SQLAlchemy ──▶ SQLite/PostgreSQL
Worker loop     ──▶ queue repository ────────────┘
                     │
                     ├──▶ source adapters  ──▶ authorized HTTP / local files
                     ├──▶ metadata providers ──▶ Jikan / AniList
                     ├──▶ media processor ──▶ ffmpeg / ffprobe
                     └──▶ telegram publisher ──▶ Bot API
```

## Pipeline

1. **Discovery** (`services/discovery.py`): the configured source adapter is
   asked for episodes. Seasons, episodes, and `source_media` rows are upserted.
   Episodes that already have an uploaded file are skipped unless `force` is
   requested.
2. **Processing** (`services/media.py`): a `PROCESS_EPISODE` task downloads (or
   copies) the source media, probes it, builds quality plans
   (`media/planning.py`), remuxes each plan into a single MKV that keeps every
   selected audio track and the subtitle tracks, embeds the poster as cover art,
   computes the SHA-256 of the result, and stores a `processed_files` row.
3. **Announcement** (`services/publish.py`): the announcement is rendered from
   the template and posted with the poster. A poster is mandatory; discovery
   refuses to start without one when `REQUIRE_ANNOUNCEMENT` is enabled.
4. **Upload** (`services/publish.py`): each processed file is uploaded as a
   Telegram document with a caption and a generated thumbnail. Uploads are
   ordered: earlier episodes first, and lower qualities of the same episode
   before higher ones. With `REQUIRE_ANNOUNCEMENT` enabled an episode upload
   waits until the announcement for that anime and channel has succeeded, so
   the announcement always reaches the channel before the episodes.

## Queue

All long-running work is stored in `queue_tasks`. A task has a `kind`, a JSON
`payload`, a `priority`, `attempts`, `max_attempts`, an optional
`dedupe_key`, and a status.

Priorities (lower value is claimed first):

| Kind | Priority |
| --- | --- |
| `PROCESS_EPISODE` | 100 |
| `PUBLISH_ANNOUNCEMENT` | 500 |
| `PUBLISH_EPISODE` | 1000 + season·100000 + episode·100 + quality rank |

The quality rank (`services/priorities.py`) orders `240p` (0) through `4320p`
(9); unknown labels rank 50. That ordering makes the lowest quality upload
first for each episode.

Deduplication keys prevent duplicate work while a task is active:

- `process:<episode_id>`
- `announcement:<anime_id>:<channel_id>`
- `publish:<processed_file_id>`

Claiming is done with a conditional `UPDATE` that only succeeds for the row
the worker observed, so a task is executed by exactly one worker.

### Task lifecycle

```text
QUEUED ──claim──▶ RUNNING ──success──▶ SUCCEEDED
   ▲                  │
   │                  ├──retryable error──▶ QUEUED (attempts+1, backoff)
   │                  │
   │                  └──permanent error──▶ DEAD
   │
   └──reset_stale / release_running / /retry
```

- **Retryable** errors (network failures, Telegram rate limits, "waiting for
  the announcement") requeue the task with exponential backoff between
  `RETRY_BASE_SECONDS` and `RETRY_MAX_SECONDS`.
- **Permanent** errors (missing poster, missing media file, missing
  permissions, duplicate checksum, skipped episode, invalid template, invalid
  source configuration) move the task to `DEAD` immediately.
- `reset_stale` returns `RUNNING` tasks older than `WORKER_STALE_SECONDS` to
  the queue, respecting each task's own `max_attempts`.
- `release_running` requeues everything that was running. It runs at worker
  startup (crash recovery) and at worker shutdown (clean stop).

### Restart recovery

On startup the worker requeues every `RUNNING` task, decrements its attempt
counter, clears the lock fields, and marks the stage as `requeued`. Because the
recovery is global, run **one** worker process per database; use
`WORKER_CONCURRENCY` for parallelism inside that process.

## Database

SQLAlchemy models live in `src/anime_fortress/db/models.py`; migrations live in
`alembic/versions/`.

| Table | Purpose |
| --- | --- |
| `sources` | Source adapter configuration (JSON) and validation status |
| `channels` | Destination channels, branding, caption template |
| `templates` | Announcement and caption templates |
| `anime` | Anime metadata, poster link, channel and source links |
| `seasons`, `episodes` | Episode catalogue and status |
| `source_media` | Discovered media items per episode |
| `processed_files` | Rendered MKV files, hashes, audio signatures |
| `audio_tracks` | Per-track language, codec, channels, bitrate |
| `queue_tasks` | Durable work queue |
| `upload_records` | Upload attempts and outcomes (with SHA-256) |
| `announcements` | Published announcements |
| `settings` | Key/value runtime settings (pause flag, counters) |
| `audit_log` | Administrative action log |

Notable constraints:

- `queue_tasks` is indexed for claiming (`status`, `priority`, `available_at`)
  and for deduplication lookups (`dedupe_key`, `status`); the dedupe rule
  itself is enforced when a task is enqueued.
- `processed_files` is unique per `(episode_id, resolution_label, audio_signature)`.
- `upload_records` has two partial unique indexes for successful uploads:
  `uq_upload_success_episode` on `(channel_id, processed_file_id)` and
  `uq_upload_success_checksum` on `(channel_id, file_sha256)`, so the same
  processed file or byte-identical file cannot be uploaded twice to the same
  channel.
- `announcements` has a partial unique index `uq_announcement_success` on
  `(anime_id, channel_id)` for successful announcements.

## Media processing

`media/inspection.py` probes containers with `ffprobe`; `media/ffmpeg.py` wraps
the `ffmpeg` binary; `media/planning.py` decides which qualities and audio
tracks to keep; `media/processor.py` executes the plan.

Rules enforced by the planner:

- Sources are grouped by height; only `PREFERRED_QUALITIES` are produced
  unless `INCLUDE_OTHER_QUALITIES` is enabled.
- The highest quality available is always kept, so nothing is silently
  dropped.
- All audio tracks are muxed into one file per quality; the container is never
  transcoded and video is never re-encoded or upscaled.
- Language labels come from the container metadata; unknown languages are
  only kept when `INCLUDE_UNKNOWN_AUDIO` is enabled.
- Commentary and extra tracks are kept when `INCLUDE_EXTRA_AUDIO` is enabled.
- The audio signature (languages plus codecs) is stored with the processed
  file so re-processing can detect changes.

After rendering, the processor verifies the output: track count, per-track
languages, and codecs must match the plan. A mismatch fails the task instead
of publishing a wrong file.

## Telegram layer

- `telegram/sender.py` defines the sender interface used by the services and
  the bot (an aiogram implementation plus a fake used in tests).
- `telegram/publisher.py` implements the delivery rules: poster photo for
  announcements, documents with caption and thumbnail for episodes, caption
  splitting at `MAX_CAPTION_LENGTH`, and message splitting at
  `MAX_MESSAGE_LENGTH`.
- `telegram/captions.py` builds the template context for announcements and
  episode captions.

## Bot

`bot/app.py` registers the routers and applies the `IsAdmin` filter to every
administrative router, so only the IDs in `ADMIN_USER_IDS` can act. Guided
flows (adding a source, adding anime, uploading a poster) use aiogram FSM
states; `/cancel` aborts a flow at any point.

The bot never performs heavy work itself: it enqueues tasks and reports queue
state, so a slow request cannot block the polling loop.

## Failure model

| Failure | Classification | Result |
| --- | --- | --- |
| Telegram timeout / 5xx | Retryable | Requeue with backoff |
| Telegram 4xx (not found, forbidden) | Permanent | `DEAD` |
| Announcement not published yet | Retryable | Requeue in 60 s |
| Earlier episode not uploaded | Retryable | Requeue in 120 s |
| Lower quality not uploaded | Retryable | Requeue in 90 s |
| Poster missing | Permanent | `DEAD` |
| Media file missing on disk | Permanent | `DEAD` |
| Identical SHA-256 already uploaded | Permanent | `DEAD` |
| Episode skipped by an administrator | Permanent | `DEAD` |
| Source URL invalid or not allowed | Permanent | `DEAD` |
| FFmpeg failure | Retryable | Requeue, episode marked `FAILED` |

## Observability

- Structured logging with optional JSON output (`LOG_JSON`).
- Secrets are redacted from logs when `REDACT_SECRETS_IN_LOGS` is enabled.
- `queue_tasks.stage` and `queue_tasks.progress` expose the live stage and
  percentage of the running task.
- `upload_records` keeps the full history with error messages.
- `/status`, `/queue`, `/history`, and `anime-fortress doctor` summarize the
  current state.
