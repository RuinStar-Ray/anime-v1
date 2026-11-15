from __future__ import annotations

import html
from collections.abc import Iterable

from aiogram.types import Message

from anime_fortress.db.models import Anime, Channel, QueueTask, Source, UploadRecord
from anime_fortress.services.status import StatusSnapshot


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def shorten(value: str, limit: int = 80) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def format_source(source: Source) -> str:
    return (
        f"<b>#{source.id}</b> {esc(source.name)} — <code>{esc(source.adapter)}</code> — "
        f"{esc(source.status.value)}"
        + (f" — <i>{esc(shorten(source.last_error or '', 120))}</i>" if source.last_error else "")
    )


def format_anime(anime: Anime) -> str:
    return (
        f"<b>#{anime.id}</b> {esc(anime.title)}"
        + (f" ({anime.release_year})" if anime.release_year else "")
        + f" — source={anime.source_id or '-'} channel={anime.channel_id or '-'}"
    )


def format_channel(channel: Channel) -> str:
    return (
        f"<b>#{channel.id}</b> {esc(channel.title)} — <code>{channel.telegram_chat_id}</code> — "
        f"{'verified' if channel.can_post else 'NOT verified'}"
        + (f" — {esc(channel.branding)}" if channel.branding else "")
    )


def format_task(task: QueueTask) -> str:
    error = f"\n   <i>{esc(shorten(task.last_error or '', 160))}</i>" if task.last_error else ""
    return (
        f"#{task.id} <code>{esc(task.kind.value)}</code> {esc(task.status.value)} "
        f"[{task.progress}%] attempt {task.attempts}/{task.max_attempts}"
        + (f" — {esc(task.stage)}" if task.stage else "")
        + error
    )


def format_upload(record: UploadRecord) -> str:
    return (
        f"#{record.id} {esc(record.file_name)} — {record.status.value} — "
        f"channel={record.channel_id}"
        + (f" msg={record.telegram_message_id}" if record.telegram_message_id else "")
        + (f" — <i>{esc(shorten(record.error or '', 100))}</i>" if record.error else "")
    )


def format_status(snapshot: StatusSnapshot) -> str:
    episodes = ", ".join(
        f"{name}={count}" for name, count in sorted(snapshot.episodes_by_status.items())
    ) or "none"
    queue = ", ".join(
        f"{name}={count}" for name, count in sorted(snapshot.queue_counts.items())
    ) or "empty"
    heartbeat = (
        f"{snapshot.heartbeat_seconds_ago}s ago"
        if snapshot.heartbeat_seconds_ago is not None
        else "unknown"
    )
    return (
        "<b>Anime Fortress status</b>\n"
        f"Sources: {snapshot.sources}\n"
        f"Anime: {snapshot.anime} | Channels: {snapshot.channels} | Seasons: {snapshot.seasons}\n"
        f"Episodes: {snapshot.episodes} ({esc(episodes)})\n"
        f"Processed files: {snapshot.processed_files} | Uploads: {snapshot.uploads} | "
        f"Announcements: {snapshot.announcements}\n"
        f"Queue: {esc(queue)}\n"
        f"Worker: {'paused' if snapshot.worker_paused else 'active'} | "
        f"running={snapshot.worker_running} | heartbeat={heartbeat}"
    )


def join_lines(lines: Iterable[str]) -> str:
    return "\n".join(lines)


async def send_long(message: Message, text: str, *, limit: int = 3800) -> None:
    if len(text) <= limit:
        await message.answer(text, parse_mode="HTML")
        return
    chunk: list[str] = []
    size = 0
    for line in text.splitlines():
        if size + len(line) + 1 > limit and chunk:
            await message.answer("\n".join(chunk), parse_mode="HTML")
            chunk = []
            size = 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        await message.answer("\n".join(chunk), parse_mode="HTML")
