from __future__ import annotations

from anime_fortress.db.enums import TemplateKind
from anime_fortress.db.models import Anime, Channel, Template
from anime_fortress.db.repositories import AnimeRepository, ChannelRepository, TemplateRepository
from anime_fortress.db.session import Database
from anime_fortress.errors import TemplateError, ValidationError
from anime_fortress.templates import TemplateEngine


class TemplateService:
    def __init__(self, db: Database, engine: TemplateEngine) -> None:
        self.db = db
        self.engine = engine

    async def create(
        self,
        *,
        name: str,
        kind: TemplateKind,
        body: str,
        description: str | None = None,
    ) -> Template:
        self.engine.validate(body)
        if len(body) > 8000:
            raise TemplateError("Template is too long (maximum 8000 characters)")
        async with self.db.session() as session:
            repo = TemplateRepository(session)
            if await repo.get_by_name(name):
                raise ValidationError(f"A template named '{name}' already exists")
            return await repo.create(
                name=name.strip(), kind=kind, body=body, description=description
            )

    async def update(self, template_id: int, body: str) -> Template:
        self.engine.validate(body)
        async with self.db.session() as session:
            repo = TemplateRepository(session)
            template = await repo.get(template_id)
            if template is None:
                raise ValidationError(f"Template {template_id} was not found")
            return await repo.update(template, body=body)

    async def list(self, kind: TemplateKind | None = None) -> list[Template]:
        async with self.db.session() as session:
            return await TemplateRepository(session).list(kind)

    async def get(self, template_id: int) -> Template | None:
        async with self.db.session() as session:
            return await TemplateRepository(session).get(template_id)

    async def default(self, kind: TemplateKind) -> Template | None:
        name = "default-announcement" if kind == TemplateKind.ANNOUNCEMENT else "default-caption"
        async with self.db.session() as session:
            return await TemplateRepository(session).get_by_name(name)

    async def set_anime_announcement_template(self, anime_id: int, template_id: int) -> Anime:
        async with self.db.session() as session:
            templates = TemplateRepository(session)
            template = await templates.get(template_id)
            if template is None:
                raise ValidationError(f"Template {template_id} was not found")
            if template.kind != TemplateKind.ANNOUNCEMENT:
                raise ValidationError("Selected template is not an announcement template")
            anime = await AnimeRepository(session).get(anime_id)
            if anime is None:
                raise ValidationError(f"Anime {anime_id} was not found")
            anime.announcement_template_id = template.id
            return anime

    async def set_channel_caption_template(self, channel_id: int, template_id: int) -> Channel:
        async with self.db.session() as session:
            templates = TemplateRepository(session)
            template = await templates.get(template_id)
            if template is None:
                raise ValidationError(f"Template {template_id} was not found")
            if template.kind != TemplateKind.CAPTION:
                raise ValidationError("Selected template is not a caption template")
            channel = await ChannelRepository(session).get(channel_id)
            if channel is None:
                raise ValidationError(f"Channel {channel_id} was not found")
            channel.caption_template_id = template.id
            return channel
