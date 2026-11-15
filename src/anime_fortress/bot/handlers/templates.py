from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from anime_fortress.app import Application
from anime_fortress.bot.formatting import esc, send_long, shorten
from anime_fortress.bot.states import TemplateStates
from anime_fortress.bot.utils import require_int
from anime_fortress.db.enums import TemplateKind
from anime_fortress.db.models import Template
from anime_fortress.errors import AnimeFortressError

router = Router(name="templates")


def parse_kind(raw: str) -> TemplateKind:
    try:
        return TemplateKind(raw.strip().lower())
    except ValueError as exc:
        raise AnimeFortressError("Template kind must be 'announcement' or 'caption'") from exc


def format_template(template: Template) -> str:
    return (
        f"<b>#{template.id}</b> {esc(template.name)} — "
        f"{esc(template.kind.value)} — {len(template.body)} chars"
    )


@router.message(Command("addtemplate"))
async def cmd_addtemplate(
    message: Message, command: CommandObject, state: FSMContext, app: Application
) -> None:
    parts = (command.args or "").split(maxsplit=2)
    if len(parts) < 2:
        await message.answer("Usage: /addtemplate <announcement|caption> <name> [body]")
        return
    try:
        kind = parse_kind(parts[0])
    except AnimeFortressError as exc:
        await message.answer(str(exc))
        return
    name = parts[1].strip()
    if len(parts) == 3:
        try:
            template = await app.templates.create(name=name, kind=kind, body=parts[2])
        except AnimeFortressError as exc:
            await message.answer(f"Could not create template: {esc(exc)}", parse_mode="HTML")
            return
        await message.answer(
            f"Template <b>#{template.id}</b> {esc(template.name)} created.", parse_mode="HTML"
        )
        return
    await state.set_state(TemplateStates.waiting_body)
    await state.update_data(template_kind=kind.value, template_name=name)
    await message.answer(
        f"Send the {kind.value} template body. Placeholders use <code>{{anime_title}}</code> "
        "syntax (see /help for all fields), HTML is allowed. Send /cancel to abort.",
        parse_mode="HTML",
    )


@router.message(TemplateStates.waiting_body)
async def template_body(message: Message, state: FSMContext, app: Application) -> None:
    if message.text is None:
        await message.answer("Template body must be text.")
        return
    data = await state.get_data()
    kind = parse_kind(str(data.get("template_kind", "")))
    name = str(data.get("template_name", ""))
    edit_id = data.get("edit_template_id")
    try:
        if edit_id:
            template = await app.templates.update(int(edit_id), message.text)
        else:
            template = await app.templates.create(name=name, kind=kind, body=message.text)
    except AnimeFortressError as exc:
        await message.answer(f"Could not save template: {esc(exc)}", parse_mode="HTML")
        return
    await state.clear()
    await message.answer(
        f"Template <b>#{template.id}</b> {esc(template.name)} saved ({len(template.body)} chars).",
        parse_mode="HTML",
    )


@router.message(Command("templates"))
async def cmd_templates(message: Message, command: CommandObject, app: Application) -> None:
    kind = None
    raw = (command.args or "").strip()
    if raw:
        try:
            kind = parse_kind(raw)
        except AnimeFortressError as exc:
            await message.answer(str(exc))
            return
    templates = await app.templates.list(kind)
    if not templates:
        await message.answer("No templates yet. Use /addtemplate.")
        return
    label = f" ({kind.value})" if kind else ""
    await send_long(
        message,
        f"<b>Templates{label}</b>\n" + "\n".join(format_template(item) for item in templates),
    )


@router.message(Command("showtemplate"))
async def cmd_showtemplate(message: Message, command: CommandObject, app: Application) -> None:
    parts = (command.args or "").split()
    if len(parts) != 1:
        await message.answer("Usage: /showtemplate <template_id>")
        return
    template_id = await require_int(message, parts[0], "template_id")
    if template_id is None:
        return
    template = await app.templates.get(template_id)
    if template is None:
        await message.answer(f"Template {template_id} was not found.")
        return
    header = (
        f"<b>#{template.id}</b> {esc(template.name)} — {esc(template.kind.value)}\n"
    )
    body = esc(template.body)
    if len(header) + len(body) <= 3800:
        await message.answer(header + f"<pre>{body}</pre>", parse_mode="HTML")
    else:
        await message.answer(header, parse_mode="HTML")
        await send_long(message, body)


@router.message(Command("edittemplate"))
async def cmd_edittemplate(
    message: Message, command: CommandObject, state: FSMContext, app: Application
) -> None:
    parts = (command.args or "").split()
    if len(parts) != 1:
        await message.answer("Usage: /edittemplate <template_id>")
        return
    template_id = await require_int(message, parts[0], "template_id")
    if template_id is None:
        return
    template = await app.templates.get(template_id)
    if template is None:
        await message.answer(f"Template {template_id} was not found.")
        return
    await state.set_state(TemplateStates.waiting_body)
    await state.update_data(
        template_kind=template.kind.value,
        template_name=template.name,
        edit_template_id=template.id,
    )
    await message.answer(
        f"Send the new body for template #{template.id} ({shorten(template.name, 60)}).",
    )


@router.message(Command("settemplate"))
async def cmd_settemplate(message: Message, command: CommandObject, app: Application) -> None:
    parts = (command.args or "").split()
    if len(parts) != 2:
        await message.answer("Usage: /settemplate <anime_id> <template_id>")
        return
    anime_id = await require_int(message, parts[0], "anime_id")
    template_id = await require_int(message, parts[1], "template_id")
    if anime_id is None or template_id is None:
        return
    try:
        anime = await app.templates.set_anime_announcement_template(anime_id, template_id)
    except AnimeFortressError as exc:
        await message.answer(str(exc))
        return
    await message.answer(f"Anime {anime.id} now uses announcement template {template_id}.")


@router.message(Command("setchanneltemplate"))
async def cmd_setchanneltemplate(
    message: Message, command: CommandObject, app: Application
) -> None:
    parts = (command.args or "").split()
    if len(parts) != 2:
        await message.answer("Usage: /setchanneltemplate <channel_id> <template_id>")
        return
    channel_id = await require_int(message, parts[0], "channel_id")
    template_id = await require_int(message, parts[1], "template_id")
    if channel_id is None or template_id is None:
        return
    try:
        channel = await app.templates.set_channel_caption_template(channel_id, template_id)
    except AnimeFortressError as exc:
        await message.answer(str(exc))
        return
    await message.answer(f"Channel {channel.id} now uses caption template {template_id}.")
