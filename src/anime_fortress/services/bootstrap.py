from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from anime_fortress.config import Settings
from anime_fortress.db.enums import TemplateKind
from anime_fortress.db.repositories import SettingRepository, TemplateRepository


async def bootstrap_defaults(session: AsyncSession, settings: Settings) -> None:
    templates = TemplateRepository(session)
    if await templates.get_by_name("default-announcement") is None:
        await templates.create(
            name="default-announcement",
            kind=TemplateKind.ANNOUNCEMENT,
            body=settings.announcement_template,
            description="Seeded from ANNOUNCEMENT_TEMPLATE",
            is_system=True,
        )
    if await templates.get_by_name("default-caption") is None:
        await templates.create(
            name="default-caption",
            kind=TemplateKind.CAPTION,
            body=settings.caption_template,
            description="Seeded from CAPTION_TEMPLATE",
            is_system=True,
        )
    settings_repo = SettingRepository(session)
    if await settings_repo.get("worker_paused") is None:
        await settings_repo.set("worker_paused", False)
    if await settings_repo.get("bootstrap_version") is None:
        await settings_repo.set("bootstrap_version", 1)
