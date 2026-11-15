from __future__ import annotations

import logging

from anime_fortress.db.base import utcnow
from anime_fortress.db.models import Channel
from anime_fortress.db.repositories import ChannelRepository
from anime_fortress.db.session import Database
from anime_fortress.errors import TelegramPublishError, ValidationError
from anime_fortress.telegram.sender import TelegramSender

logger = logging.getLogger(__name__)


class ChannelService:
    def __init__(self, db: Database, sender: TelegramSender) -> None:
        self.db = db
        self.sender = sender

    async def register(
        self, chat: str | int, *, branding: str | None = None
    ) -> tuple[Channel, bool, str]:
        info = await self.sender.get_chat(chat)
        if info.chat_type not in {"channel", "supergroup"}:
            raise ValidationError(
                f"Chat '{info.title}' is a {info.chat_type}; register a Telegram channel or supergroup"
            )
        member = await self.sender.get_bot_member(info.chat_id)
        async with self.db.session() as session:
            repo = ChannelRepository(session)
            channel = await repo.create_or_update(
                telegram_chat_id=info.chat_id,
                title=info.title,
                username=info.username,
                branding=branding,
            )
            channel.is_verified = member.can_post
            channel.can_post = member.can_post
            channel.last_verified_at = utcnow()
        message = (
            f"Channel '{info.title}' registered with posting permissions verified."
            if member.can_post
            else (
                f"Channel '{info.title}' was saved, but the bot cannot post "
                f"(status={member.status}). Promote the bot to administrator with 'Post messages'."
            )
        )
        if not member.can_post:
            raise TelegramPublishError(message)
        logger.info("Registered channel %s (%s)", info.title, info.chat_id)
        return channel, member.can_post, message

    async def verify(self, channel_id: int) -> tuple[Channel, bool]:
        async with self.db.session() as session:
            channel = await ChannelRepository(session).get(channel_id)
            if channel is None:
                raise ValidationError(f"Channel {channel_id} was not found")
            chat_id = channel.telegram_chat_id
        info = await self.sender.get_chat(chat_id)
        member = await self.sender.get_bot_member(chat_id)
        async with self.db.session() as session:
            channel = await ChannelRepository(session).get(channel_id)
            if channel is None:
                raise ValidationError(f"Channel {channel_id} was not found")
            channel.title = info.title
            channel.username = info.username
            channel.can_post = member.can_post
            channel.is_verified = member.can_post
            channel.last_verified_at = utcnow()
            return channel, member.can_post

    async def set_branding(self, channel_id: int, branding: str) -> Channel:
        cleaned = branding.strip()
        if len(cleaned) > 120:
            raise ValidationError("Branding must be 120 characters or fewer")
        async with self.db.session() as session:
            channel = await ChannelRepository(session).get(channel_id)
            if channel is None:
                raise ValidationError(f"Channel {channel_id} was not found")
            channel.branding = cleaned
            return channel

    async def list(self) -> list[Channel]:
        async with self.db.session() as session:
            return await ChannelRepository(session).list()

    async def get(self, channel_id: int) -> Channel | None:
        async with self.db.session() as session:
            return await ChannelRepository(session).get(channel_id)
