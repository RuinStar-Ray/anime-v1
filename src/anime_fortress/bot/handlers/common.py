from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from anime_fortress.app import Application
from anime_fortress.bot.formatting import (
    esc,
    format_anime,
    format_channel,
    format_source,
    format_status,
    send_long,
)
from anime_fortress.config import Settings

public_router = Router(name="public")
admin_router = Router(name="admin")
fallback_router = Router(name="fallback")

HELP_TEXT = """<b>Anime Fortress admin commands</b>

<b>Sources</b>
/addsource — register a source (guided)
/listsources, /checksource &lt;id&gt;
<b>Anime</b>
/addanime — add anime via Jikan/AniList or manually (guided)
/anime, /episodes &lt;anime_id&gt;
/setmeta &lt;anime_id&gt; &lt;field&gt; &lt;value&gt;
/setsource &lt;anime_id&gt; &lt;source_id&gt;
/setlanguages &lt;anime_id&gt; &lt;en,ja,...&gt;
/setquality &lt;anime_id&gt; &lt;1080p,720p,...&gt;
/setextraaudio &lt;anime_id&gt; &lt;on|off&gt;
<b>Channels</b>
/addchannel &lt;@username|chat_id&gt; [branding]
/channels, /checkchannel &lt;id&gt;
/setchannel &lt;anime_id&gt; &lt;channel_id&gt;
/setbranding &lt;channel_id&gt; &lt;branding&gt;
<b>Templates</b>
/addtemplate &lt;announcement|caption&gt; &lt;name&gt;
/templates [kind], /showtemplate &lt;id&gt;, /edittemplate &lt;id&gt;
/settemplate &lt;anime_id&gt; &lt;template_id&gt;
/setchanneltemplate &lt;channel_id&gt; &lt;template_id&gt;
<b>Images</b>
/setposter &lt;anime_id&gt;
/setthumbnail &lt;anime_id&gt;
/setepthumbnail &lt;episode_id&gt;
/setchannelthumbnail &lt;channel_id&gt;
<b>Publishing</b>
/preview &lt;anime_id&gt;
/process &lt;anime_id&gt; [force]
/announce &lt;anime_id&gt;
/queue, /status, /history [anime_id]
/pause, /resume, /retry &lt;task_id|all&gt;
/skip &lt;episode_id&gt;
/cancel — leave the current guided flow
/id — show your Telegram IDs
"""

ADMIN_MENU = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="Status", callback_data="nav:status"),
            InlineKeyboardButton(text="Queue", callback_data="nav:queue"),
        ],
        [
            InlineKeyboardButton(text="Sources", callback_data="nav:sources"),
            InlineKeyboardButton(text="Anime", callback_data="nav:anime"),
        ],
        [
            InlineKeyboardButton(text="Channels", callback_data="nav:channels"),
            InlineKeyboardButton(text="Help", callback_data="nav:help"),
        ],
        [
            InlineKeyboardButton(text="Pause", callback_data="nav:pause"),
            InlineKeyboardButton(text="Resume", callback_data="nav:resume"),
        ],
    ]
)


@public_router.message(Command("start"))
async def cmd_start(message: Message, settings: Settings) -> None:
    if not settings.is_admin(message.from_user.id if message.from_user else 0):
        await message.answer(
            "This is a private administrative bot for anime publishing. Access denied."
        )
        return
    await message.answer(
        "<b>Anime Fortress</b>\nAutomated anime publishing control panel.\n"
        "Use /help for the full command list.",
        parse_mode="HTML",
        reply_markup=ADMIN_MENU,
    )


@public_router.message(Command("help"))
async def cmd_help(message: Message, settings: Settings) -> None:
    if not settings.is_admin(message.from_user.id if message.from_user else 0):
        await message.answer("This is a private administrative bot. Access denied.")
        return
    await send_long(message, HELP_TEXT)


@public_router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(
        f"user_id: <code>{message.from_user.id if message.from_user else '-'}</code>\n"
        f"chat_id: <code>{message.chat.id}</code>",
        parse_mode="HTML",
    )


@admin_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    await message.answer("Flow cancelled." if current else "Nothing is in progress.")


@admin_router.callback_query(F.data.startswith("nav:"))
async def nav_callback(callback: CallbackQuery, app: Application) -> None:
    action = (callback.data or "").split(":", 1)[1]
    if callback.message is None:
        await callback.answer("This message is no longer available.")
        return
    if action == "status":
        await send_long(callback.message, format_status(await app.status.snapshot()))
    elif action == "queue":
        counts = await app.queue.counts()
        tasks = await app.queue.recent(10)
        lines = ["<b>Queue</b>", esc(", ".join(f"{k}={v}" for k, v in sorted(counts.items())))]
        from anime_fortress.bot.formatting import format_task

        lines.extend(format_task(task) for task in tasks)
        await send_long(callback.message, "\n".join(lines))
    elif action == "sources":
        sources = await app.sources.list()
        text = "\n".join(format_source(item) for item in sources) or "No sources yet."
        await send_long(callback.message, "<b>Sources</b>\n" + text)
    elif action == "anime":
        anime = await app.anime.list()
        text = "\n".join(format_anime(item) for item in anime) or "No anime yet."
        await send_long(callback.message, "<b>Anime</b>\n" + text)
    elif action == "channels":
        channels = await app.channels.list()
        text = "\n".join(format_channel(item) for item in channels) or "No channels yet."
        await send_long(callback.message, "<b>Channels</b>\n" + text)
    elif action == "help":
        await send_long(callback.message, HELP_TEXT)
    elif action == "pause":
        await app.queue.pause()
        await callback.message.answer("Worker paused. Running tasks finish, new tasks wait.")
    elif action == "resume":
        await app.queue.resume()
        await callback.message.answer("Worker resumed.")
    await callback.answer()


@fallback_router.message(F.text.startswith("/"))
async def unknown_command(message: Message, settings: Settings) -> None:
    if not settings.is_admin(message.from_user.id if message.from_user else 0):
        await message.answer("This is a private administrative bot. Access denied.")
        return
    await message.answer("Unknown command. Use /help.")


@fallback_router.message(F.text)
async def unexpected_text(message: Message, settings: Settings, state: FSMContext) -> None:
    if not settings.is_admin(message.from_user.id if message.from_user else 0):
        return
    if await state.get_state():
        await message.answer("Send the requested input, or /cancel to abort.")
    else:
        await message.answer("Use /help for available commands.")


@fallback_router.callback_query()
async def unexpected_callback(callback: CallbackQuery, settings: Settings) -> None:
    if not settings.is_admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await callback.answer("This action is no longer available.")
