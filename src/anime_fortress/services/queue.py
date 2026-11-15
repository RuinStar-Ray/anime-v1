from __future__ import annotations

from anime_fortress.db.models import QueueTask
from anime_fortress.db.repositories import QueueRepository, SettingRepository
from anime_fortress.db.session import Database


class QueueService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def is_paused(self) -> bool:
        async with self.db.session() as session:
            return await SettingRepository(session).is_worker_paused()

    async def pause(self) -> None:
        async with self.db.session() as session:
            await SettingRepository(session).set("worker_paused", True)

    async def resume(self) -> None:
        async with self.db.session() as session:
            await SettingRepository(session).set("worker_paused", False)

    async def counts(self) -> dict[str, int]:
        async with self.db.session() as session:
            return await QueueRepository(session).counts()

    async def recent(self, limit: int = 15) -> list[QueueTask]:
        async with self.db.session() as session:
            return await QueueRepository(session).recent(limit)

    async def retry(self, task_id: int) -> QueueTask | None:
        async with self.db.session() as session:
            return await QueueRepository(session).retry(task_id)

    async def retry_failed(self) -> int:
        async with self.db.session() as session:
            return await QueueRepository(session).retry_failed()

    async def cancel(self, task_id: int) -> QueueTask | None:
        async with self.db.session() as session:
            return await QueueRepository(session).cancel(task_id)
