from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from pathlib import Path

from anime_fortress.config import Settings
from anime_fortress.errors import TelegramPublishError
from anime_fortress.telegram.sender import SentMessage, TelegramSender
from anime_fortress.templates import split_announcement_messages

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnnouncementDelivery:
    message_id: int
    detail_message_id: int | None
    file_id: str | None


class TelegramPublisher:
    def __init__(self, sender: TelegramSender, settings: Settings) -> None:
        self.sender = sender
        self.settings = settings

    async def deliver_announcement(
        self,
        *,
        chat_id: int,
        title: str,
        text: str,
        poster_path: Path | None,
    ) -> AnnouncementDelivery:
        if not text.strip():
            raise TelegramPublishError("Announcement text is empty")
        if poster_path is None or not poster_path.exists():
            raise TelegramPublishError(
                "A custom anime poster is required before publishing the announcement"
            )
        if len(text) <= self.settings.max_caption_length:
            sent = await self.sender.send_photo(chat_id, poster_path, caption=text)
            return AnnouncementDelivery(sent.message_id, None, sent.file_id)
        short_caption = (
            f"<b>{html.escape(title)}</b>\n\n"
            "<i>Full anime details are in the message below.</i>"
        )
        photo = await self.sender.send_photo(chat_id, poster_path, caption=short_caption)
        messages = split_announcement_messages(text, self.settings.max_message_length)
        first_detail_id: int | None = None
        previous_id = photo.message_id
        for index, chunk in enumerate(messages):
            use_html = len(messages) == 1
            sent = await self.sender.send_message(
                chat_id,
                chunk,
                parse_mode="HTML" if use_html else None,
                reply_to_message_id=previous_id,
            )
            previous_id = sent.message_id
            if index == 0:
                first_detail_id = sent.message_id
        return AnnouncementDelivery(photo.message_id, first_detail_id, photo.file_id)

    async def deliver_episode(
        self,
        *,
        chat_id: int,
        path: Path,
        filename: str,
        caption: str,
        thumbnail_path: Path | None,
    ) -> SentMessage:
        if not path.exists():
            raise TelegramPublishError(f"Processed media file is missing: {path.name}")
        size = path.stat().st_size
        if size > self.settings.max_upload_bytes:
            raise TelegramPublishError(
                f"{filename} is {size / (1024 * 1024):.1f} MB, above MAX_UPLOAD_MB="
                f"{self.settings.max_upload_mb:.1f}. Increase MAX_UPLOAD_MB when using a local "
                "Bot API server, or reduce the file size."
            )
        return await self.sender.send_document(
            chat_id,
            path,
            filename=filename,
            caption=caption,
            thumbnail_path=thumbnail_path,
        )
