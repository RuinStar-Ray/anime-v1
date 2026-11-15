from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from datetime import UTC, datetime
from typing import Any

from anime_fortress.config import Settings
from anime_fortress.db.enums import QueueStatus, TaskKind
from anime_fortress.db.models import QueueTask
from anime_fortress.db.repositories import QueueRepository, SettingRepository
from anime_fortress.db.session import Database
from anime_fortress.errors import (
    AnimeFortressError,
    MediaError,
    PermanentTaskError,
    RetryableTaskError,
    SourceError,
    TelegramPublishError,
    TemplateError,
    ValidationError,
)
from anime_fortress.services.media import MediaService
from anime_fortress.services.publish import PublishService

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        media_service: MediaService,
        publish_service: PublishService,
    ) -> None:
        self.db = db
        self.settings = settings
        self.media_service = media_service
        self.publish_service = publish_service
        self.worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.running: dict[int, asyncio.Task[None]] = {}
        self._stop = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None
        self._anime_locks: dict[int, tuple[asyncio.Lock, int]] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def recover(self) -> int:
        async with self.db.session() as session:
            recovered = await QueueRepository(session).release_running()
            await SettingRepository(session).set("worker_running", True)
            await SettingRepository(session).set("worker_heartbeat", datetime.now(UTC).isoformat())
        if recovered:
            logger.warning("Requeued %s task(s) left running by a previous worker", recovered)
        return recovered

    async def start(self) -> None:
        self._stop.clear()
        await self.recover()
        self._loop_task = asyncio.create_task(self._loop(), name="anime-fortress-worker")
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="worker-heartbeat")
        logger.info("Worker %s started with concurrency=%s", self.worker_id, self.settings.worker_concurrency)

    async def stop(self) -> None:
        self._stop.set()
        if self._loop_task:
            await asyncio.gather(self._loop_task, return_exceptions=True)
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
        pending = list(self.running.values())
        if pending:
            logger.info("Waiting for %s running task(s) to finish", len(pending))
            _done, still_pending = await asyncio.wait(pending, timeout=20)
            for task in still_pending:
                task.cancel()
            await asyncio.gather(*still_pending, return_exceptions=True)
        async with self.db.session() as session:
            await QueueRepository(session).release_running()
            await SettingRepository(session).set("worker_running", False)
        logger.info("Worker %s stopped", self.worker_id)

    async def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            try:
                async with self.db.session() as session:
                    await SettingRepository(session).set(
                        "worker_heartbeat", datetime.now(UTC).isoformat()
                    )
            except Exception:
                logger.exception("Could not update worker heartbeat")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=15)
            except TimeoutError:
                continue

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                async with self.db.session() as session:
                    paused = await SettingRepository(session).is_worker_paused()
                if paused:
                    await asyncio.sleep(self.settings.worker_poll_interval_seconds)
                    continue
                self._reap_finished()
                slots = self.settings.worker_concurrency - len(self.running)
                for _ in range(max(slots, 0)):
                    task = await self._claim()
                    if task is None:
                        break
                    worker_task = asyncio.create_task(
                        self._execute(task), name=f"queue-task-{task.id}"
                    )
                    self.running[task.id] = worker_task
                await asyncio.sleep(self.settings.worker_poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Worker loop error")
                await asyncio.sleep(min(self.settings.worker_poll_interval_seconds * 2, 30))

    async def _claim(self) -> QueueTask | None:
        async with self.db.session() as session:
            return await QueueRepository(session).claim_next(self.worker_id)

    def _reap_finished(self) -> None:
        for task_id, task in list(self.running.items()):
            if task.done():
                self.running.pop(task_id, None)
                if task.exception():
                    logger.error("Task %s crashed: %s", task_id, task.exception())

    async def run_once(self) -> int:
        """Process all currently available tasks once; useful for tests and maintenance."""
        processed = 0
        while True:
            self._reap_finished()
            if len(self.running) >= self.settings.worker_concurrency:
                await asyncio.gather(*list(self.running.values()), return_exceptions=True)
                self._reap_finished()
            task = await self._claim()
            if task is None:
                break
            processed += 1
            await self._execute(task)
        if self.running:
            await asyncio.gather(*list(self.running.values()), return_exceptions=True)
            self._reap_finished()
        return processed

    def _acquire_anime_lock(self, anime_id: int) -> asyncio.Lock:
        entry = self._anime_locks.get(anime_id)
        if entry is None:
            lock = asyncio.Lock()
            count = 0
        else:
            lock, count = entry
        self._anime_locks[anime_id] = (lock, count + 1)
        return lock

    def _release_anime_lock(self, anime_id: int) -> None:
        entry = self._anime_locks.get(anime_id)
        if entry is None:
            return
        lock, count = entry
        if count <= 1:
            self._anime_locks.pop(anime_id, None)
        else:
            self._anime_locks[anime_id] = (lock, count - 1)

    async def _progress(self, task_id: int, stage: str, progress: int) -> None:
        async with self.db.session() as session:
            await QueueRepository(session).set_progress(
                task_id, stage=stage, progress=progress
            )

    async def _execute(self, task: QueueTask) -> None:
        anime_id = int(task.payload.get("anime_id") or 0)
        lock = self._acquire_anime_lock(anime_id) if anime_id else None
        stage = {
            TaskKind.PROCESS_EPISODE: "preparing",
            TaskKind.PUBLISH_ANNOUNCEMENT: "announcement",
            TaskKind.PUBLISH_EPISODE: "uploading",
        }.get(task.kind, "working")
        try:
            await self._progress(task.id, stage, 2)
            if lock:
                async with lock:
                    result = await self._dispatch(task)
            else:
                result = await self._dispatch(task)
        except asyncio.CancelledError:
            raise
        except RetryableTaskError as exc:
            status = await self._fail(
                task,
                str(exc),
                retryable=True,
                retry_after=exc.retry_after_seconds,
            )
            logger.warning("Task %s retryable failure (%s): %s", task.id, status.value, exc)
        except (
            PermanentTaskError,
            TemplateError,
            TelegramPublishError,
            MediaError,
            SourceError,
            ValidationError,
        ) as exc:
            await self._fail(task, str(exc), retryable=False)
            logger.error("Task %s failed permanently: %s", task.id, exc)
        except AnimeFortressError as exc:
            await self._fail(task, str(exc), retryable=True)
            logger.warning("Task %s failed: %s", task.id, exc)
        except Exception as exc:
            logger.exception("Unexpected task failure")
            await self._fail(task, f"{type(exc).__name__}: {exc}", retryable=True)
        else:
            async with self.db.session() as session:
                await QueueRepository(session).complete(task.id, result or {})
            logger.info("Task %s (%s) completed", task.id, task.kind.value)
        finally:
            if anime_id:
                self._release_anime_lock(anime_id)

    async def _dispatch(self, task: QueueTask) -> dict[str, Any]:
        if task.kind == TaskKind.PROCESS_EPISODE:
            episode_id = int(task.payload["episode_id"])

            async def progress(stage: str, percent: int) -> None:
                await self._progress(task.id, stage, percent)

            summary = await self.media_service.process_episode(
                episode_id, progress=progress, task_id=task.id
            )
            return {
                "episode_id": summary.episode_id,
                "qualities": summary.planned_qualities,
                "processed_file_ids": summary.processed_file_ids,
                "queued_uploads": summary.queued_uploads,
            }
        if task.kind == TaskKind.PUBLISH_ANNOUNCEMENT:
            message_id = await self.publish_service.publish_announcement(
                int(task.payload["anime_id"])
            )
            return {"message_id": message_id}
        if task.kind == TaskKind.PUBLISH_EPISODE:
            message_id = await self.publish_service.publish_episode(
                int(task.payload["processed_file_id"])
            )
            return {"message_id": message_id}
        raise PermanentTaskError(f"Unsupported task kind: {task.kind}")

    async def _fail(
        self,
        task: QueueTask,
        error: str,
        *,
        retryable: bool,
        retry_after: int | None = None,
    ) -> QueueStatus:
        async with self.db.session() as session:
            return await QueueRepository(session).fail(
                task.id,
                error,
                retryable=retryable,
                retry_after_seconds=retry_after,
                base_retry=self.settings.retry_base_seconds,
                max_retry=self.settings.retry_max_seconds,
            )
