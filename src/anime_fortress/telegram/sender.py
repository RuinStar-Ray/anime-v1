from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import FSInputFile

from anime_fortress.errors import RetryableTaskError, TelegramPublishError


@dataclass(frozen=True)
class SentMessage:
    message_id: int
    chat_id: int
    file_id: str | None = None


@dataclass(frozen=True)
class ChatInfo:
    chat_id: int
    title: str
    username: str | None
    chat_type: str


@dataclass(frozen=True)
class BotMemberInfo:
    status: str
    can_post: bool


class TelegramSender(Protocol):
    async def send_document(
        self,
        chat_id: int,
        path: Path,
        *,
        filename: str,
        caption: str | None,
        thumbnail_path: Path | None = None,
    ) -> SentMessage: ...

    async def send_photo(
        self,
        chat_id: int,
        path: Path,
        *,
        caption: str | None,
        parse_mode: str | None = "HTML",
    ) -> SentMessage: ...

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = "HTML",
        reply_to_message_id: int | None = None,
    ) -> SentMessage: ...

    async def get_chat(self, chat: int | str) -> ChatInfo: ...

    async def get_bot_member(self, chat_id: int) -> BotMemberInfo: ...


class AiogramSender:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send_document(
        self,
        chat_id: int,
        path: Path,
        *,
        filename: str,
        caption: str | None,
        thumbnail_path: Path | None = None,
    ) -> SentMessage:
        try:
            message = await self.bot.send_document(
                chat_id=chat_id,
                document=FSInputFile(path, filename=filename),
                thumbnail=FSInputFile(thumbnail_path) if thumbnail_path else None,
                caption=caption,
                parse_mode=ParseMode.HTML if caption else None,
                supports_streaming=False,
                request_timeout=600,
            )
        except TelegramRetryAfter as exc:
            raise RetryableTaskError(
                f"Telegram flood limit hit; retry after {exc.retry_after}s",
                retry_after_seconds=exc.retry_after,
            ) from exc
        except TelegramNetworkError as exc:
            raise RetryableTaskError(f"Telegram network error: {exc}") from exc
        except TelegramAPIError as exc:
            raise TelegramPublishError(f"Telegram rejected the document upload: {exc}") from exc
        document = message.document
        return SentMessage(
            message_id=message.message_id,
            chat_id=message.chat.id,
            file_id=document.file_id if document else None,
        )

    async def send_photo(
        self,
        chat_id: int,
        path: Path,
        *,
        caption: str | None,
        parse_mode: str | None = "HTML",
    ) -> SentMessage:
        try:
            message = await self.bot.send_photo(
                chat_id=chat_id,
                photo=FSInputFile(path),
                caption=caption,
                parse_mode=ParseMode.HTML if parse_mode == "HTML" and caption else None,
                request_timeout=180,
            )
        except TelegramRetryAfter as exc:
            raise RetryableTaskError(
                f"Telegram flood limit hit; retry after {exc.retry_after}s",
                retry_after_seconds=exc.retry_after,
            ) from exc
        except TelegramNetworkError as exc:
            raise RetryableTaskError(f"Telegram network error: {exc}") from exc
        except TelegramAPIError as exc:
            raise TelegramPublishError(f"Telegram rejected the poster: {exc}") from exc
        photo = message.photo[-1] if message.photo else None
        return SentMessage(
            message_id=message.message_id,
            chat_id=message.chat.id,
            file_id=photo.file_id if photo else None,
        )

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = "HTML",
        reply_to_message_id: int | None = None,
    ) -> SentMessage:
        try:
            message = await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML if parse_mode == "HTML" else None,
                reply_to_message_id=reply_to_message_id,
                request_timeout=120,
            )
        except TelegramRetryAfter as exc:
            raise RetryableTaskError(
                f"Telegram flood limit hit; retry after {exc.retry_after}s",
                retry_after_seconds=exc.retry_after,
            ) from exc
        except TelegramNetworkError as exc:
            raise RetryableTaskError(f"Telegram network error: {exc}") from exc
        except TelegramAPIError as exc:
            raise TelegramPublishError(f"Telegram rejected the announcement text: {exc}") from exc
        return SentMessage(message_id=message.message_id, chat_id=message.chat.id)

    async def get_chat(self, chat: int | str) -> ChatInfo:
        try:
            info = await self.bot.get_chat(chat)
        except TelegramAPIError as exc:
            raise TelegramPublishError(f"Could not resolve Telegram chat '{chat}': {exc}") from exc
        return ChatInfo(
            chat_id=info.id,
            title=info.title or info.full_name or str(info.id),
            username=info.username,
            chat_type=str(info.type),
        )

    async def get_bot_member(self, chat_id: int) -> BotMemberInfo:
        try:
            member = await self.bot.get_chat_member(chat_id, self.bot.id)
        except TelegramNetworkError as exc:
            raise RetryableTaskError(f"Could not verify bot permissions: {exc}") from exc
        except TelegramAPIError as exc:
            raise TelegramPublishError(f"Could not verify bot permissions: {exc}") from exc
        status = str(member.status)
        can_post = False
        if status == "creator":
            can_post = True
        elif status == "administrator":
            can_post_value = getattr(member, "can_post_messages", None)
            can_post = True if can_post_value is None else bool(can_post_value)
        return BotMemberInfo(status=status, can_post=can_post)
