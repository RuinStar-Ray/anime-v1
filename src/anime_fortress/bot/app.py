from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, ErrorEvent

from anime_fortress.app import Application
from anime_fortress.bot.formatting import esc
from anime_fortress.bot.guards import IsAdmin
from anime_fortress.bot.handlers import (
    anime,
    channels,
    common,
    posters,
    publishing,
    sources,
    templates,
)
from anime_fortress.errors import AnimeFortressError

logger = logging.getLogger(__name__)

PUBLIC_COMMANDS = [
    BotCommand(command="start", description="Open the control panel"),
    BotCommand(command="help", description="List admin commands"),
    BotCommand(command="id", description="Show your Telegram IDs"),
]

ADMIN_COMMANDS = PUBLIC_COMMANDS + [
    BotCommand(command="addsource", description="Register a source"),
    BotCommand(command="listsources", description="List sources"),
    BotCommand(command="checksource", description="Validate a source"),
    BotCommand(command="addanime", description="Add anime"),
    BotCommand(command="anime", description="List anime"),
    BotCommand(command="episodes", description="List episodes"),
    BotCommand(command="setmeta", description="Correct metadata field"),
    BotCommand(command="setsource", description="Assign source to anime"),
    BotCommand(command="setlanguages", description="Set audio language preferences"),
    BotCommand(command="setquality", description="Set quality preferences"),
    BotCommand(command="setextraaudio", description="Include/exclude commentary audio"),
    BotCommand(command="addchannel", description="Register a channel"),
    BotCommand(command="channels", description="List channels"),
    BotCommand(command="checkchannel", description="Verify channel permissions"),
    BotCommand(command="setchannel", description="Assign anime to channel"),
    BotCommand(command="setbranding", description="Set channel branding"),
    BotCommand(command="addtemplate", description="Create a template"),
    BotCommand(command="templates", description="List templates"),
    BotCommand(command="showtemplate", description="Show a template"),
    BotCommand(command="edittemplate", description="Edit a template"),
    BotCommand(command="settemplate", description="Assign announcement template"),
    BotCommand(command="setchanneltemplate", description="Assign caption template"),
    BotCommand(command="setposter", description="Upload anime poster"),
    BotCommand(command="setthumbnail", description="Upload anime thumbnail"),
    BotCommand(command="setepthumbnail", description="Upload episode thumbnail"),
    BotCommand(command="setchannelthumbnail", description="Upload channel thumbnail"),
    BotCommand(command="preview", description="Preview announcement"),
    BotCommand(command="process", description="Discover and queue episodes"),
    BotCommand(command="announce", description="Queue announcement"),
    BotCommand(command="queue", description="Show queue"),
    BotCommand(command="status", description="Show status"),
    BotCommand(command="pause", description="Pause worker"),
    BotCommand(command="resume", description="Resume worker"),
    BotCommand(command="retry", description="Retry failed tasks"),
    BotCommand(command="history", description="Upload history"),
    BotCommand(command="skip", description="Skip an episode"),
    BotCommand(command="cancel", description="Cancel current flow"),
]

ADMIN_ROUTERS = [
    common.admin_router,
    sources.router,
    anime.router,
    channels.router,
    templates.router,
    posters.router,
    publishing.router,
]


def build_dispatcher(app: Application) -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["app"] = app
    dispatcher["settings"] = app.settings
    by_admin = IsAdmin()
    for router in ADMIN_ROUTERS:
        router.message.filter(by_admin)
        router.callback_query.filter(by_admin)
    dispatcher.include_router(common.public_router)
    for router in ADMIN_ROUTERS:
        dispatcher.include_router(router)
    dispatcher.include_router(common.fallback_router)

    @dispatcher.errors()
    async def handle_error(event: ErrorEvent) -> None:
        error = event.exception
        logger.exception("Unhandled bot error", exc_info=error)
        if isinstance(error, AnimeFortressError):
            text = str(error)[:700]
        else:
            text = f"Unexpected error: {type(error).__name__}. Check the logs."
        update = event.update
        try:
            if update.message is not None:
                await update.message.answer(f"⚠ {esc(text)}", parse_mode="HTML")
            elif update.callback_query is not None:
                await update.callback_query.answer(text[:200], show_alert=True)
        except Exception:
            logger.warning("Could not deliver error message to the administrator")

    return dispatcher


async def set_bot_commands(bot: Bot, app: Application) -> None:
    await bot.set_my_commands(PUBLIC_COMMANDS, scope=BotCommandScopeDefault())
    for admin_id in app.settings.admin_user_ids:
        try:
            await bot.set_my_commands(
                ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except TelegramBadRequest:
            logger.warning("Could not set admin command scope for user %s", admin_id)
