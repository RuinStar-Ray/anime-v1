from __future__ import annotations

import logging
from typing import Any

from anime_fortress.db.base import utcnow
from anime_fortress.db.enums import SourceStatus
from anime_fortress.db.models import Source
from anime_fortress.db.repositories import SourceRepository
from anime_fortress.db.session import Database
from anime_fortress.errors import AnimeFortressError, SourceError
from anime_fortress.sources.base import SourceRuntime, ValidationResult
from anime_fortress.sources.registry import SourceRegistry

logger = logging.getLogger(__name__)


class SourceService:
    def __init__(
        self,
        db: Database,
        registry: SourceRegistry,
        runtime: SourceRuntime,
    ) -> None:
        self.db = db
        self.registry = registry
        self.runtime = runtime

    async def add(self, *, name: str, adapter: str, config: dict[str, Any]) -> Source:
        adapter_instance = self.registry.get(adapter)
        if not isinstance(config, dict):
            raise SourceError("Source configuration must be a JSON object")
        async with self.db.session() as session:
            repo = SourceRepository(session)
            if await repo.get_by_name(name):
                raise SourceError(f"A source named '{name}' already exists")
            source = await repo.create(name=name.strip(), adapter=adapter_instance.name, config=config)
            return source

    async def list(self) -> list[Source]:
        async with self.db.session() as session:
            return await SourceRepository(session).list()

    async def get(self, source_id: int) -> Source | None:
        async with self.db.session() as session:
            return await SourceRepository(session).get(source_id)

    async def validate(self, source_id: int) -> tuple[Source | None, ValidationResult]:
        async with self.db.session() as session:
            repo = SourceRepository(session)
            source = await repo.get(source_id)
            if source is None:
                return None, ValidationResult(ok=False, message=f"Source {source_id} was not found")
            if not source.enabled:
                return source, ValidationResult(ok=False, message="Source is disabled")
            try:
                adapter = self.registry.get(source.adapter)
                result = await adapter.validate(dict(source.config or {}), self.runtime)
            except AnimeFortressError as exc:
                result = ValidationResult(ok=False, message=str(exc))
            except Exception as exc:  # defensive boundary for third-party adapters
                logger.exception("Unexpected source validation failure")
                result = ValidationResult(ok=False, message=f"Unexpected adapter error: {exc}")
            source.status = SourceStatus.VALID if result.ok else SourceStatus.INVALID
            source.last_error = None if result.ok else result.message[:4000]
            source.last_validated_at = utcnow()
            return source, result
