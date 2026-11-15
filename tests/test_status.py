from __future__ import annotations

from datetime import UTC, datetime, timedelta

from anime_fortress.bot.formatting import format_status
from anime_fortress.config import Settings
from anime_fortress.db.enums import TaskKind
from anime_fortress.db.repositories import QueueRepository, SettingRepository
from anime_fortress.db.session import Database
from anime_fortress.services.status import StatusService


async def test_status_snapshot(db: Database, settings: Settings, seed_data) -> None:
    seed = await seed_data(db, settings=settings)
    async with db.session() as session:
        await QueueRepository(session).enqueue(
            kind=TaskKind.PROCESS_EPISODE, payload={"anime_id": seed["anime_id"]}
        )
        await SettingRepository(session).set("worker_paused", True)
        await SettingRepository(session).set("worker_running", True)
        await SettingRepository(session).set(
            "worker_heartbeat", (datetime.now(UTC) - timedelta(seconds=65)).isoformat()
        )

    snapshot = await StatusService(db).snapshot()
    assert snapshot.sources == 0
    assert snapshot.anime == 1
    assert snapshot.channels == 1
    assert snapshot.seasons == 1
    assert snapshot.episodes == 1
    assert snapshot.processed_files == 1
    assert snapshot.episodes_by_status == {"discovered": 1}
    assert snapshot.queue_counts == {"queued": 1}
    assert snapshot.worker_paused is True
    assert snapshot.worker_running is True
    assert snapshot.heartbeat_seconds_ago is not None
    assert snapshot.heartbeat_seconds_ago >= 64

    text = format_status(snapshot)
    assert "Anime Fortress status" in text
    assert "Seasons: 1" in text
    assert "Worker: paused" in text
    assert "queued=1" in text


async def test_status_history_helpers(db: Database, settings: Settings, seed_data) -> None:
    await seed_data(db, settings=settings)
    status = StatusService(db)
    assert await status.upload_history() == []
    assert await status.recent_tasks() == []
