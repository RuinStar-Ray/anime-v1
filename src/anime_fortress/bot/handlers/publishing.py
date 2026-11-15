from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import FSInputFile, Message
from sqlalchemy import select

from anime_fortress.app import Application
from anime_fortress.bot.formatting import (
    esc,
    format_status,
    format_task,
    format_upload,
    send_long,
)
from anime_fortress.bot.utils import require_int
from anime_fortress.db.enums import ACTIVE_QUEUE_STATUSES, EpisodeStatus, QueueStatus, TaskKind
from anime_fortress.db.models import QueueTask
from anime_fortress.db.repositories import (
    AnimeRepository,
    EpisodeRepository,
    QueueRepository,
)
from anime_fortress.errors import AnimeFortressError
from anime_fortress.services.priorities import PRIORITY_ANNOUNCEMENT

logger = logging.getLogger(__name__)
router = Router(name="publishing")


@router.message(Command("preview"))
async def cmd_preview(message: Message, command: CommandObject, app: Application) -> None:
    parts = (command.args or "").split()
    if len(parts) != 1:
        await message.answer("Usage: /preview <anime_id>")
        return
    anime_id = await require_int(message, parts[0], "anime_id")
    if anime_id is None:
        return
    try:
        rendered = await app.presentation.render_announcement(anime_id)
    except AnimeFortressError as exc:
        await message.answer(f"Preview failed: {esc(exc)}", parse_mode="HTML")
        return
    if rendered.poster_path is not None:
        await message.answer_photo(FSInputFile(rendered.poster_path))
    await send_long(message, rendered.text)
    if rendered.warnings:
        await send_long(
            message, "<b>Warnings</b>\n" + "\n".join(f"• {esc(item)}" for item in rendered.warnings)
        )


@router.message(Command("process"))
async def cmd_process(message: Message, command: CommandObject, app: Application) -> None:
    parts = (command.args or "").split()
    if not parts:
        anime = await app.anime.list()
        listing = "\n".join(f"#{item.id} {esc(item.title)}" for item in anime[:30])
        await message.answer(
            "Usage: /process <anime_id> [force]\n" + (listing or "No anime yet."),
            parse_mode="HTML",
        )
        return
    if len(parts) > 2 or (len(parts) == 2 and parts[1].lower() != "force"):
        await message.answer("Usage: /process <anime_id> [force]")
        return
    anime_id = await require_int(message, parts[0], "anime_id")
    if anime_id is None:
        return
    force = len(parts) == 2
    try:
        summary = await app.discovery.discover_for_anime(anime_id, force=force)
    except AnimeFortressError as exc:
        await message.answer(f"Discovery failed: {esc(exc)}", parse_mode="HTML")
        return
    lines = [
        f"<b>Discovery complete for anime #{summary.anime_id}</b>",
        f"Episodes found: {summary.episodes_found}",
        f"Queued for processing: {summary.episodes_queued}",
        f"Skipped (uploaded/skipped): {summary.episodes_skipped}",
        f"Media items: {summary.media_items}",
        f"Announcement queued: {'yes' if summary.announcement_queued else 'no'}",
    ]
    if summary.warnings:
        lines.append("Warnings:\n" + "\n".join(f"• {esc(item)}" for item in summary.warnings))
    await send_long(message, "\n".join(lines))


@router.message(Command("announce"))
async def cmd_announce(message: Message, command: CommandObject, app: Application) -> None:
    parts = (command.args or "").split()
    if len(parts) != 1:
        await message.answer("Usage: /announce <anime_id>")
        return
    anime_id = await require_int(message, parts[0], "anime_id")
    if anime_id is None:
        return
    async with app.db.session() as session:
        anime = await AnimeRepository(session).get_full(anime_id)
        if anime is None:
            await message.answer(f"Anime {anime_id} was not found.")
            return
        if anime.channel is None:
            await message.answer("Assign a destination channel with /setchannel first.")
            return
        channel_id = anime.channel.id
        task, created = await QueueRepository(session).enqueue(
            kind=TaskKind.PUBLISH_ANNOUNCEMENT,
            payload={"anime_id": anime_id, "channel_id": channel_id},
            dedupe_key=f"announcement:{anime_id}:{channel_id}",
            priority=PRIORITY_ANNOUNCEMENT,
            max_attempts=app.settings.task_max_attempts,
        )
        task_id = task.id
    await message.answer(
        f"Announcement task #{task_id} {'queued' if created else 'already pending'} for "
        f"anime {anime_id} -> channel {channel_id}."
    )


@router.message(Command("queue"))
async def cmd_queue(message: Message, app: Application) -> None:
    counts = await app.queue.counts()
    tasks = await app.queue.recent(15)
    lines = ["<b>Queue</b>"]
    lines.append(esc(", ".join(f"{key}={value}" for key, value in sorted(counts.items()))) or "empty")
    if tasks:
        lines.extend(format_task(task) for task in tasks)
    else:
        lines.append("No tasks.")
    await send_long(message, "\n".join(lines))


@router.message(Command("status"))
async def cmd_status(message: Message, app: Application) -> None:
    await send_long(message, format_status(await app.status.snapshot()))


@router.message(Command("pause"))
async def cmd_pause(message: Message, app: Application) -> None:
    await app.queue.pause()
    await message.answer("Worker paused. Running tasks finish; new tasks wait.")


@router.message(Command("resume"))
async def cmd_resume(message: Message, app: Application) -> None:
    await app.queue.resume()
    await message.answer("Worker resumed.")


@router.message(Command("retry"))
async def cmd_retry(message: Message, command: CommandObject, app: Application) -> None:
    parts = (command.args or "").split()
    if len(parts) != 1:
        await message.answer("Usage: /retry <task_id|all>")
        return
    if parts[0].lower() == "all":
        count = await app.queue.retry_failed()
        await message.answer(f"Requeued {count} failed task(s).")
        return
    task_id = await require_int(message, parts[0], "task_id")
    if task_id is None:
        return
    task = await app.queue.retry(task_id)
    if task is None:
        await message.answer(f"Task {task_id} was not found or is not retryable.")
        return
    await message.answer(f"Task {task.id} requeued (attempts reset to 0).")


@router.message(Command("history"))
async def cmd_history(message: Message, command: CommandObject, app: Application) -> None:
    parts = (command.args or "").split()
    anime_id = None
    if parts:
        anime_id = await require_int(message, parts[0], "anime_id")
        if anime_id is None:
            return
    records = await app.status.upload_history(limit=20, anime_id=anime_id)
    if not records:
        await message.answer("No upload history yet.")
        return
    await send_long(
        message,
        "<b>Upload history</b>\n" + "\n".join(format_upload(record) for record in records),
    )


@router.message(Command("skip"))
async def cmd_skip(message: Message, command: CommandObject, app: Application) -> None:
    parts = (command.args or "").split(maxsplit=1)
    if not parts:
        await message.answer("Usage: /skip <episode_id> [reason]")
        return
    episode_id = await require_int(message, parts[0], "episode_id")
    if episode_id is None:
        return
    reason = parts[1].strip() if len(parts) > 1 else "Skipped by administrator"
    async with app.db.session() as session:
        episode = await EpisodeRepository(session).get_full(episode_id)
        if episode is None:
            await message.answer(f"Episode {episode_id} was not found.")
            return
        if episode.status == EpisodeStatus.UPLOADED:
            await message.answer("That episode is already uploaded and cannot be skipped.")
            return
        episode.status = EpisodeStatus.SKIPPED
        episode.skipped_reason = reason[:255]
        active_tasks = list(
            (
                await session.execute(
                    select(QueueTask).where(QueueTask.status.in_(ACTIVE_QUEUE_STATUSES))
                )
            )
            .scalars()
            .all()
        )
        cancelled = 0
        for task in active_tasks:
            payload = task.payload or {}
            if payload.get("episode_id") == episode_id:
                task.status = QueueStatus.CANCELLED
                cancelled += 1
        episode_number = episode.number
        season_number = episode.season.number
    await message.answer(
        f"Episode {episode_id} (S{season_number:02d}E{episode_number:02d}) marked skipped."
        + (f" Cancelled {cancelled} queued task(s)." if cancelled else "")
    )
