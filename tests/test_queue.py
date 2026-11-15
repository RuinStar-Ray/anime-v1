from __future__ import annotations

from datetime import timedelta

from anime_fortress.db.base import utcnow
from anime_fortress.db.enums import QueueStatus, TaskKind
from anime_fortress.db.models import QueueTask
from anime_fortress.db.repositories import QueueRepository
from anime_fortress.db.session import Database
from anime_fortress.services.priorities import (
    PRIORITY_ANNOUNCEMENT,
    PRIORITY_PROCESS_EPISODE,
    publish_priority,
)


async def _enqueue(db: Database, *, kind: TaskKind = TaskKind.PROCESS_EPISODE, **kwargs):
    async with db.session() as session:
        return await QueueRepository(session).enqueue(
            kind=kind,
            payload={"anime_id": 1, **kwargs.pop("payload", {})},
            **kwargs,
        )


async def test_enqueue_dedupes_active_tasks(db: Database) -> None:
    first, created = await _enqueue(db, dedupe_key="episode:1:process")
    second, created_again = await _enqueue(db, dedupe_key="episode:1:process")
    assert created is True
    assert created_again is False
    assert first.id == second.id


async def test_claim_order_priority_and_complete(db: Database) -> None:
    low, _ = await _enqueue(db, priority=500, payload={"name": "low"})
    high, _ = await _enqueue(db, priority=100, payload={"name": "high"})
    async with db.session() as session:
        repo = QueueRepository(session)
        claimed = await repo.claim_next("worker-a")
        assert claimed is not None
        assert claimed.id == high.id
        assert claimed.status == QueueStatus.RUNNING
        assert claimed.attempts == 1
        await repo.complete(claimed.id, {"ok": True})
        claimed_again = await repo.claim_next("worker-a")
        assert claimed_again is not None
        assert claimed_again.id == low.id
        await repo.complete(claimed_again.id)
        assert await repo.claim_next("worker-a") is None


async def test_progress_and_release_running(db: Database) -> None:
    task, _ = await _enqueue(db, dedupe_key="progress-task")
    async with db.session() as session:
        repo = QueueRepository(session)
        claimed = await repo.claim_next("worker-a")
        assert claimed is not None
        await repo.set_progress(claimed.id, stage="downloading", progress=45)
        stored = await repo.get(claimed.id)
        assert stored is not None
        assert stored.stage == "downloading"
        assert stored.progress == 45

        released = await repo.release_running()
        assert released == 1
        stored = await repo.get(claimed.id)
        assert stored is not None
        assert stored.status == QueueStatus.QUEUED
        assert stored.attempts == 0
        assert stored.stage == "requeued"
        assert stored.locked_by is None


async def test_retryable_failure_schedules_retry_and_dead_after_max(db: Database) -> None:
    task, _ = await _enqueue(db, max_attempts=2)
    async with db.session() as session:
        repo = QueueRepository(session)
        claimed = await repo.claim_next("worker-a")
        assert claimed is not None
        status = await repo.fail(
            claimed.id, "temporary", retryable=True, retry_after_seconds=5
        )
        assert status == QueueStatus.QUEUED
        stored = await repo.get(claimed.id)
        assert stored is not None
        assert stored.available_at > utcnow()
        assert stored.last_error == "temporary"

    async with db.session() as session:
        stored = await session.get(QueueTask, claimed.id)
        assert stored is not None
        stored.available_at = utcnow() - timedelta(seconds=1)
    async with db.session() as session:
        repo = QueueRepository(session)
        claimed = await repo.claim_next("worker-a")
        assert claimed is not None
        assert claimed.attempts == 2
        status = await repo.fail(claimed.id, "still failing", retryable=True)
        assert status == QueueStatus.DEAD
        stored = await repo.get(claimed.id)
        assert stored is not None
        assert stored.finished_at is not None


async def test_permanent_failure_and_manual_retry(db: Database) -> None:
    task, _ = await _enqueue(db, max_attempts=5)
    async with db.session() as session:
        repo = QueueRepository(session)
        claimed = await repo.claim_next("worker-a")
        assert claimed is not None
        status = await repo.fail(claimed.id, "bad input", retryable=False)
        assert status == QueueStatus.FAILED
        retried = await repo.retry(claimed.id)
        assert retried is not None
        assert retried.status == QueueStatus.QUEUED
        assert retried.attempts == 0
        assert retried.last_error is None


async def test_reset_stale_requeues_or_kills(db: Database) -> None:
    fresh, _ = await _enqueue(db, max_attempts=1)
    dead, _ = await _enqueue(db, max_attempts=1)
    async with db.session() as session:
        for task_id in (fresh.id, dead.id):
            task = await session.get(QueueTask, task_id)
            assert task is not None
            task.status = QueueStatus.RUNNING
            task.locked_at = utcnow() - timedelta(hours=2)
            task.locked_by = "crashed-worker"
        task = await session.get(QueueTask, dead.id)
        assert task is not None
        task.attempts = 1
    async with db.session() as session:
        recovered = await QueueRepository(session).reset_stale(60, max_attempts=5)
        assert recovered == 2
        fresh_row = await session.get(QueueTask, fresh.id)
        dead_row = await session.get(QueueTask, dead.id)
        assert fresh_row is not None and fresh_row.status == QueueStatus.QUEUED
        assert dead_row is not None and dead_row.status == QueueStatus.DEAD


async def test_queue_counts_and_history(db: Database) -> None:
    await _enqueue(db, priority=1)
    async with db.session() as session:
        repo = QueueRepository(session)
        assert await repo.counts() == {"queued": 1}
        assert await repo.active_count() == 1
        recent = await repo.recent(5)
        assert len(recent) == 1


def test_priority_contract() -> None:
    assert PRIORITY_PROCESS_EPISODE < PRIORITY_ANNOUNCEMENT
    assert PRIORITY_ANNOUNCEMENT < publish_priority(1, 1, "480p")
    assert publish_priority(1, 1, "480p") < publish_priority(1, 1, "1080p")
    assert publish_priority(1, 2, "480p") > publish_priority(1, 1, "1080p")
    assert publish_priority(2, 1, "480p") > publish_priority(1, 99, "1080p")
    assert publish_priority(1, 1, "1080p") == 1000 + 100000 + 100 + 6
    assert publish_priority(1, 1, "1080p") < publish_priority(1, 1, "1440p")
