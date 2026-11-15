# Troubleshooting

Start with the built-in diagnostics:

```bash
uv run anime-fortress doctor
```

It checks directory permissions, database connectivity and schema, FFmpeg and
FFprobe availability, installed source adapters, metadata providers, the bot
token, admin IDs, and the media library allowlist. It exits non-zero when a
fatal problem is found.

## The bot does not respond

| Symptom | Cause | Fix |
| --- | --- | --- |
| "This is a private administrative bot. Access denied." | Your Telegram ID is not in `ADMIN_USER_IDS` | Send `/id`, add the ID, restart the bot |
| Bot replies but ignores commands | Wrong bot instance or token revoked | Recreate the token with BotFather, update `BOT_TOKEN` |
| "Unauthorized" in the logs | Token is invalid or belongs to another bot | Verify `BOT_TOKEN`; no trailing spaces or quotes |
| Bot polls but the commands list is short | Bot commands are scoped per chat | Expected for non-admin users |

## The bot cannot post to the channel

- The bot must be an **administrator** of the channel, not just a member.
- It needs the "post messages" permission.
- `/checkchannel <channel_id>` reports the live permission state.
- The error "The bot cannot post to '<channel>' (status=...)" is a permanent
  error: fix the permission and requeue the task with `/retry <task_id>` or
  `/retry all`.

## Discovery finds nothing

- `/checksource <source_id>` re-validates the source and shows the last error.
- `local_library` requires `MEDIA_LIBRARY_ROOTS` to be set and the source root
  to be inside it. Windows separates roots with `;`, POSIX with `:`.
- The episode regex must expose an `episode` group. The default is
  `(?:S(?P<season>\d{1,2}))?[^0-9]*(?:E|Ep|Episode)[ ._-]*(?P<episode>\d{1,4})`.
- RSS entries need a media enclosure and a title that matches the regexes.
- "The source returned no episodes" means the adapter parsed the feed or
  directory but found nothing usable.
- `Upload a custom poster with /setposter before processing` means discovery
  refuses to run without a poster while `REQUIRE_ANNOUNCEMENT` is enabled.

## Processing fails

| Message | Meaning | Action |
| --- | --- | --- |
| `ffmpeg exited with code ...` | FFmpeg could not build the output | Check the source file integrity and disk space |
| `ffprobe not found` | FFprobe is not on `PATH` | Install FFmpeg or set `FFPROBE_PATH` |
| `Rendered file failed verification` | The output does not match the plan | Inspect the source file; retry after fixing it |
| `No source media was discovered for this episode` | The episode has no `source_media` rows | Re-run `/process` or check the source configuration |
| `Identical file (matching SHA-256) was already uploaded` | The same bytes were uploaded to that channel before | Expected dedupe; delete the row manually if it was a mistake |

After a failure the episode is marked `FAILED`. Re-running `/process
<anime_id> [force]` resets the episode to `DISCOVERED` and queues it again.
`/retry <task_id>` or `/retry all` requeues failed tasks without re-running
discovery.

## Uploads are rejected

- `MAX_UPLOAD_MB` must stay below the limit of the Bot API you use
  (49 MB for the public Bot API, 2 GB for a local Bot API server).
- `ensure_uploadable` raises a permanent error when the file is too large;
  lower `PREFERRED_QUALITIES` for that anime.
- `TelegramPublishError` with HTTP 429 means you hit a rate limit; the task is
  retried with backoff.
- A file that was already uploaded to the same channel is rejected by the
  checksum index.

## The queue is stuck

- `/pause` stops claiming new tasks; running tasks finish. `/resume` continues.
- `/queue` shows the task list. A task in `QUEUED` that never starts usually
  means the worker is not running or the worker is paused.
- A task in `RUNNING` for a long time is either genuinely processing (large
  remux) or was orphaned by a crash. Restarting the worker requeues it.
- `WORKER_STALE_SECONDS` (default 900) controls how long a task may stay
  `RUNNING` before `reset_stale` returns it to the queue.
- Do not run two worker processes against the same database: both would try to
  requeue and claim the same tasks.

## Episode ordering messages

- "Waiting for earlier episode S01E02 to finish uploading" — strict ordering
  is enabled and an earlier episode is still pending.
- "Waiting for lower-quality version(s) to upload first" — the 1080p upload
  waits for the 720p upload of the same episode.
- "Announcement is not published yet; episode upload will wait" — the
  announcement task has not succeeded yet. Check that a poster exists and that
  the bot can post to the channel.

## Caption or template errors

- "Unknown template placeholder(s)" — the body uses a field that does not
  exist. Use `/showtemplate <id>` to inspect and fix the body.
- "Rendered caption is N characters; Telegram allows 1024" — shorten the
  caption template or the filename pattern.
- "Template is too long (maximum 8000 characters)" — shorten the template.

## Database problems

- "database schema missing tables" — run `uv run anime-fortress init-db`.
- "database is locked" (SQLite) — another process is writing. Do not run two
  workers, and avoid running heavy migrations while the worker is active.
- Migration errors — check `alembic_version` and the latest revision; take a
  backup before migrating again.

## Media directory problems

- "Processed media file is missing" — the rendered file was deleted after
  processing. Re-run `/process <anime_id> force`.
- "Local source root is outside the configured MEDIA_LIBRARY_ROOTS allowlist" —
  add the root to the environment variable and restart the worker.
- "Media URL is not retrievable: HTTP 403" — the source needs credentials;
  configure `headers_env` with the name of an environment variable that holds
  the headers.

## Logs

Set `LOG_LEVEL=DEBUG` for more detail and `LOG_JSON=true` for structured
output that a log collector can parse. `REDACT_SECRETS_IN_LOGS` (enabled by
default) removes tokens and headers from log output.

## Getting help

Include the output of `anime-fortress doctor`, the relevant `/queue` and
`/history` output, and the exact error text from the logs. Never paste the bot
token or source credentials.
