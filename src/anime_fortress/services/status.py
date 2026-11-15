from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select

from anime_fortress.db.base import utcnow
from anime_fortress.db.models import (
    Anime,
    AnnouncementRecord,
    Channel,
    Episode,
    ProcessedFile,
    QueueTask,
    Season,
    Setting,
    Source,
    UploadRecord,
)
from anime_fortress.db.repositories import QueueRepository, UploadRepository
from anime_fortress.db.session import Database


@dataclass
class StatusSnapshot:
    sources: int
    anime: int
    channels: int
    seasons: int
    episodes: int
    processed_files: int
    uploads: int
    announcements: int
    episodes_by_status: dict[str, int] = field(default_factory=dict)
    queue_counts: dict[str, int] = field(default_factory=dict)
    worker_paused: bool = False
    worker_running: bool = False
    heartbeat_seconds_ago: int | None = None


class StatusService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def snapshot(self) -> StatusSnapshot:
        async with self.db.session() as session:
            counts: dict[str, int] = {}
            for name, model in (
                ("sources", Source),
                ("anime", Anime),
                ("channels", Channel),
                ("episodes", Episode),
                ("processed", ProcessedFile),
                ("uploads", UploadRecord),
                ("announcements", AnnouncementRecord),
            ):
                counts[name] = int(await session.scalar(select(func.count(model.id))) or 0)
            season_count = int(await session.scalar(select(func.count(Season.id))) or 0)
            episode_rows = await session.execute(
                select(Episode.status, func.count(Episode.id)).group_by(Episode.status)
            )
            episodes_by_status = {
                str(row[0].value if hasattr(row[0], "value") else row[0]): int(row[1])
                for row in episode_rows
            }
            queue_counts = await QueueRepository(session).counts()
            paused_setting = await session.get(Setting, "worker_paused")
            running_setting = await session.get(Setting, "worker_running")
            heartbeat_setting = await session.get(Setting, "worker_heartbeat")
        heartbeat_age: int | None = None
        if heartbeat_setting and isinstance(heartbeat_setting.value, str):
            try:
                heartbeat = datetime.fromisoformat(heartbeat_setting.value)
                if heartbeat.tzinfo is None:
                    heartbeat = heartbeat.replace(tzinfo=UTC)
                heartbeat_age = int((utcnow() - heartbeat).total_seconds())
            except ValueError:
                heartbeat_age = None
        return StatusSnapshot(
            sources=counts["sources"],
            anime=counts["anime"],
            channels=counts["channels"],
            seasons=season_count,
            episodes=counts["episodes"],
            episodes_by_status=episodes_by_status,
            processed_files=counts["processed"],
            uploads=counts["uploads"],
            announcements=counts["announcements"],
            queue_counts=queue_counts,
            worker_paused=bool(paused_setting.value) if paused_setting else False,
            worker_running=bool(running_setting.value) if running_setting else False,
            heartbeat_seconds_ago=heartbeat_age,
        )

    async def upload_history(self, limit: int = 15, anime_id: int | None = None) -> list[UploadRecord]:
        async with self.db.session() as session:
            return await UploadRepository(session).history(limit=limit, anime_id=anime_id)

    async def recent_tasks(self, limit: int = 15) -> list[QueueTask]:
        async with self.db.session() as session:
            return await QueueRepository(session).recent(limit)
