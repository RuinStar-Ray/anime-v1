from __future__ import annotations

from pathlib import Path

import pytest

from anime_fortress.config import Settings
from anime_fortress.db.enums import EpisodeStatus, ProcessedStatus, UploadStatus
from anime_fortress.db.models import (
    AnnouncementRecord,
    Episode,
    ProcessedFile,
    UploadRecord,
)
from anime_fortress.db.session import Database
from anime_fortress.errors import PermanentTaskError, RetryableTaskError, TelegramPublishError
from anime_fortress.media.ffmpeg import FFmpeg
from anime_fortress.media.inspection import FFprobe
from anime_fortress.media.processor import MediaProcessor
from anime_fortress.services.posters import PosterService
from anime_fortress.services.presentation import PresentationService
from anime_fortress.services.publish import PublishService
from anime_fortress.telegram.publisher import TelegramPublisher
from anime_fortress.telegram.sender import BotMemberInfo, SentMessage
from anime_fortress.templates import TemplateEngine


class FakeSender:
    def __init__(self, *, can_post: bool = True, fail_with: Exception | None = None) -> None:
        self.can_post = can_post
        self.fail_with = fail_with
        self.documents: list[dict] = []
        self.photos: list[dict] = []
        self.messages: list[dict] = []
        self.next_message_id = 100

    async def get_bot_member(self, chat_id: int) -> BotMemberInfo:
        return BotMemberInfo(
            status="administrator" if self.can_post else "member", can_post=self.can_post
        )

    async def get_chat(self, chat: int | str):
        raise NotImplementedError

    async def send_document(self, chat_id: int, path: Path, **kwargs) -> SentMessage:
        if self.fail_with:
            raise self.fail_with
        self.documents.append({"chat_id": chat_id, "path": path, **kwargs})
        self.next_message_id += 1
        return SentMessage(self.next_message_id, chat_id, file_id="doc-file-id")

    async def send_photo(self, chat_id: int, path: Path, **kwargs) -> SentMessage:
        if self.fail_with:
            raise self.fail_with
        self.photos.append({"chat_id": chat_id, "path": path, **kwargs})
        self.next_message_id += 1
        return SentMessage(self.next_message_id, chat_id, file_id="photo-file-id")

    async def send_message(self, chat_id: int, text: str, **kwargs) -> SentMessage:
        if self.fail_with:
            raise self.fail_with
        self.messages.append({"chat_id": chat_id, "text": text, **kwargs})
        self.next_message_id += 1
        return SentMessage(self.next_message_id, chat_id)


def _service(
    db: Database, settings: Settings, sender: FakeSender
) -> tuple[PublishService, PosterService]:
    engine = TemplateEngine(
        missing_value=settings.missing_value_text,
        cleanup_blank_lines=settings.cleanup_blank_lines,
        max_caption_length=settings.max_caption_length,
    )
    presentation = PresentationService(db, settings, engine)
    probe = FFprobe(settings)
    ffmpeg = FFmpeg(settings)
    posters = PosterService(db, settings, probe, ffmpeg)
    processor = MediaProcessor(
        settings,
        probe,
        ffmpeg,
        http=object(),  # type: ignore[arg-type]
    )
    publisher = TelegramPublisher(sender, settings)
    service = PublishService(
        db, settings, presentation, sender, publisher, posters, processor
    )
    return service, posters


async def _make_thumbnail(seed: dict) -> None:
    path = Path(seed["thumbnail_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-thumbnail")


async def test_announcement_publish_and_idempotency(
    db: Database, settings: Settings, seed_data
) -> None:
    seed = await seed_data(db, settings=settings, with_poster=True)
    sender = FakeSender()
    service, _ = _service(db, settings, sender)

    message_id = await service.publish_announcement(seed["anime_id"])
    assert message_id > 0
    assert len(sender.photos) == 1
    assert sender.photos[0]["caption"]

    again = await service.publish_announcement(seed["anime_id"])
    assert again == message_id
    assert len(sender.photos) == 1

    async with db.session() as session:
        records = (
            await session.execute(
                AnnouncementRecord.__table__.select().where(
                    AnnouncementRecord.anime_id == seed["anime_id"]
                )
            )
        ).all()
    assert len(records) == 1
    assert records[0].status == UploadStatus.SUCCESS


async def test_announcement_requires_poster_and_permission(
    db: Database, settings: Settings, seed_data
) -> None:
    seed = await seed_data(db, settings=settings, with_poster=False)
    sender = FakeSender()
    service, _ = _service(db, settings, sender)
    with pytest.raises(TelegramPublishError, match="poster"):
        await service.publish_announcement(seed["anime_id"])
    assert sender.photos == []

    denied = FakeSender(can_post=False)
    service2, _ = _service(db, settings, denied)
    with pytest.raises(PermanentTaskError, match="cannot post"):
        await service2.publish_announcement(seed["anime_id"])


async def test_episode_publish_success_updates_records(
    db: Database, settings: Settings, seed_data
) -> None:
    seed = await seed_data(db, settings=settings, with_poster=True)
    await _make_thumbnail(seed)
    sender = FakeSender()
    service, _ = _service(db, settings, sender)

    message_id = await service.publish_episode(seed["processed_ids"][0])
    assert message_id is not None
    assert len(sender.documents) == 1
    document = sender.documents[0]
    assert document["filename"] == "S01E01-720p-0.mkv"
    assert Path(document["thumbnail_path"]).exists()

    async with db.session() as session:
        processed = await session.get(ProcessedFile, seed["processed_ids"][0])
        episode = await session.get(Episode, seed["episode_id"])
        uploads = (await session.execute(UploadRecord.__table__.select())).all()
        assert processed is not None and processed.status == ProcessedStatus.UPLOADED
        assert processed.telegram_file_id == "doc-file-id"
        assert episode is not None and episode.status == EpisodeStatus.UPLOADED
        assert len(uploads) == 1
        assert uploads[0].status == UploadStatus.SUCCESS
        assert uploads[0].file_sha256 == processed.sha256

    assert await service.publish_episode(seed["processed_ids"][0]) is None
    assert len(sender.documents) == 1


async def test_episode_requires_announcement_when_configured(
    db: Database, settings: Settings, seed_data
) -> None:
    settings.require_announcement = True
    seed = await seed_data(db, settings=settings, with_poster=True)
    await _make_thumbnail(seed)
    sender = FakeSender()
    service, _ = _service(db, settings, sender)
    with pytest.raises(RetryableTaskError, match="Announcement"):
        await service.publish_episode(seed["processed_ids"][0])
    assert sender.documents == []

    await service.publish_announcement(seed["anime_id"])
    message_id = await service.publish_episode(seed["processed_ids"][0])
    assert message_id is not None
    assert len(sender.documents) == 1


async def test_episode_records_failed_attempt(
    db: Database, settings: Settings, seed_data
) -> None:
    seed = await seed_data(db, settings=settings, with_poster=True)
    await _make_thumbnail(seed)
    sender = FakeSender(fail_with=TelegramPublishError("telegram rejected upload"))
    service, _ = _service(db, settings, sender)
    with pytest.raises(TelegramPublishError):
        await service.publish_episode(seed["processed_ids"][0])
    async with db.session() as session:
        records = (await session.execute(UploadRecord.__table__.select())).all()
        assert len(records) == 1
        assert records[0].status == UploadStatus.FAILED
        assert "telegram rejected" in records[0].error


async def test_episode_checksum_dedupe(
    db: Database, settings: Settings, seed_data
) -> None:
    seed = await seed_data(
        db, settings=settings, with_poster=True, resolution="720p", processed_count=2
    )
    await _make_thumbnail(seed)
    async with db.session() as session:
        first = await session.get(ProcessedFile, seed["processed_ids"][0])
        second = await session.get(ProcessedFile, seed["processed_ids"][1])
        assert first is not None and second is not None
        second.sha256 = first.sha256
    sender = FakeSender()
    service, _ = _service(db, settings, sender)
    await service.publish_episode(seed["processed_ids"][0])
    with pytest.raises(PermanentTaskError, match="SHA-256"):
        await service.publish_episode(seed["processed_ids"][1])


async def test_episode_missing_media_is_permanent(
    db: Database, settings: Settings, seed_data
) -> None:
    seed = await seed_data(db, settings=settings, with_poster=True, with_media=False)
    await _make_thumbnail(seed)
    sender = FakeSender()
    service, _ = _service(db, settings, sender)
    Path(seed["media_path"]).unlink(missing_ok=True)
    with pytest.raises(PermanentTaskError, match="missing"):
        await service.publish_episode(seed["processed_ids"][0])


async def test_episode_skipped_is_permanent(
    db: Database, settings: Settings, seed_data
) -> None:
    seed = await seed_data(db, settings=settings, with_poster=True)
    await _make_thumbnail(seed)
    async with db.session() as session:
        episode = await session.get(Episode, seed["episode_id"])
        assert episode is not None
        episode.status = EpisodeStatus.SKIPPED
    sender = FakeSender()
    service, _ = _service(db, settings, sender)
    with pytest.raises(PermanentTaskError, match="skipped"):
        await service.publish_episode(seed["processed_ids"][0])
    assert sender.documents == []


async def test_lower_quality_must_upload_first(
    db: Database, settings: Settings, seed_data
) -> None:
    seed = await seed_data(
        db, settings=settings, with_poster=True, resolution="720p", processed_count=2
    )
    await _make_thumbnail(seed)
    async with db.session() as session:
        first = await session.get(ProcessedFile, seed["processed_ids"][0])
        second = await session.get(ProcessedFile, seed["processed_ids"][1])
        assert first is not None and second is not None
        first.height = 480
        first.resolution_label = "480p"
    sender = FakeSender()
    service, _ = _service(db, settings, sender)
    with pytest.raises(RetryableTaskError, match="lower-quality"):
        await service.publish_episode(seed["processed_ids"][1])
    assert sender.documents == []
