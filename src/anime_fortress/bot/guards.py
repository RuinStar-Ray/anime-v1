from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from anime_fortress.config import Settings


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery, settings: Settings) -> bool:
        user = event.from_user
        return bool(user and settings.is_admin(user.id))


class IsPrivateAdminChat(BaseFilter):
    async def __call__(self, event: Message, settings: Settings) -> bool:
        user = event.from_user
        return bool(user and settings.is_admin(user.id) and event.chat.type == "private")
