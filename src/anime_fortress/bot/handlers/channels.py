from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from anime_fortress.app import Application
from anime_fortress.bot.formatting import esc, format_channel, send_long
from anime_fortress.bot.utils import require_int
from anime_fortress.errors import AnimeFortressError

router = Router(name="channels")


@router.message(Command("addchannel"))
async def cmd_addchannel(message: Message, command: CommandObject, app: Application) -> None:
    parts = (command.args or "").split(maxsplit=1)
    if not parts:
        await message.answer("Usage: /addchannel <@username|chat_id> [branding]")
        return
    branding = parts[1].strip() if len(parts) > 1 else None
    try:
        channel, can_post, text = await app.channels.register(parts[0], branding=branding)
    except AnimeFortressError as exc:
        await message.answer(str(exc))
        return
    await message.answer(
        f"Channel <b>#{channel.id}</b> {esc(channel.title)} "
        f"(<code>{channel.telegram_chat_id}</code>)\n{esc(text)}",
        parse_mode="HTML",
    )


@router.message(Command("channels"))
async def cmd_channels(message: Message, app: Application) -> None:
    channels = await app.channels.list()
    if not channels:
        await message.answer("No channels yet. Use /addchannel @username.")
        return
    await send_long(message, "<b>Channels</b>\n" + "\n".join(format_channel(item) for item in channels))


@router.message(Command("checkchannel"))
async def cmd_checkchannel(message: Message, command: CommandObject, app: Application) -> None:
    parts = (command.args or "").split()
    if len(parts) != 1:
        await message.answer("Usage: /checkchannel <channel_id>")
        return
    channel_id = await require_int(message, parts[0], "channel_id")
    if channel_id is None:
        return
    try:
        channel, can_post = await app.channels.verify(channel_id)
    except AnimeFortressError as exc:
        await message.answer(str(exc))
        return
    await message.answer(
        f"Channel {channel.id} ({esc(channel.title)}): "
        f"{'can post' if can_post else 'CANNOT post'}.",
        parse_mode="HTML",
    )


@router.message(Command("setchannel"))
async def cmd_setchannel(message: Message, command: CommandObject, app: Application) -> None:
    parts = (command.args or "").split()
    if len(parts) != 2:
        await message.answer("Usage: /setchannel <anime_id> <channel_id>")
        return
    anime_id = await require_int(message, parts[0], "anime_id")
    channel_id = await require_int(message, parts[1], "channel_id")
    if anime_id is None or channel_id is None:
        return
    try:
        anime = await app.anime.set_channel(anime_id, channel_id)
    except AnimeFortressError as exc:
        await message.answer(str(exc))
        return
    await message.answer(f"Anime {anime.id} will publish to channel {channel_id}.")


@router.message(Command("setbranding"))
async def cmd_setbranding(message: Message, command: CommandObject, app: Application) -> None:
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Usage: /setbranding <channel_id> <branding>")
        return
    channel_id = await require_int(message, parts[0], "channel_id")
    if channel_id is None:
        return
    try:
        channel = await app.channels.set_branding(channel_id, parts[1])
    except AnimeFortressError as exc:
        await message.answer(str(exc))
        return
    await message.answer(f"Channel {channel.id} branding set to: {esc(channel.branding)}", parse_mode="HTML")
