from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from anime_fortress.config import Settings
from anime_fortress.db.base import Base


class Database:
    def __init__(self, engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.engine = engine
        self.session_factory = session_factory

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def drop_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)

    async def dispose(self) -> None:
        await self.engine.dispose()


def create_database(settings: Settings, *, echo: bool = False) -> Database:
    engine = create_async_engine(
        settings.database_url,
        echo=echo,
        pool_pre_ping=True,
        connect_args={"timeout": 30} if settings.database_url.startswith("sqlite") else {},
    )

    if settings.database_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection: object, _connection_record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    return Database(engine, factory)


async def check_database(database: Database) -> bool:
    async with database.session() as session:
        result = await session.execute(text("SELECT 1"))
        return result.scalar_one() == 1
