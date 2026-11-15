from __future__ import annotations

import json
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from anime_fortress.app import Application
from anime_fortress.bot.formatting import esc, format_source, send_long
from anime_fortress.bot.states import AddSourceStates
from anime_fortress.bot.utils import require_int
from anime_fortress.errors import SourceError

router = Router(name="sources")


def parse_config(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SourceError(f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise SourceError("Source configuration must be a JSON object")
    return value


async def create_source(message: Message, app: Application, name: str, adapter: str, config_raw: str) -> None:
    name = name.strip()
    if not name:
        await message.answer("Source name cannot be empty.")
        return
    if len(name) > 100:
        await message.answer("Source name must be 100 characters or fewer.")
        return
    try:
        config = parse_config(config_raw)
        source = await app.sources.add(name=name, adapter=adapter.lower(), config=config)
    except SourceError as exc:
        await message.answer(f"Could not add source: {esc(exc)}", parse_mode="HTML")
        return
    source, result = await app.sources.validate(source.id)
    await message.answer(
        f"Source <b>#{source.id}</b> {esc(source.name)} added with adapter "
        f"<code>{esc(source.adapter)}</code>.\nValidation: <b>{'OK' if result.ok else 'FAILED'}</b> — "
        f"{esc(result.message)}",
        parse_mode="HTML",
    )


@router.message(Command("addsource"))
async def cmd_addsource(message: Message, command: object, app: Application, state: FSMContext) -> None:
    args = getattr(command, "args", None)
    if args:
        parts = args.split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("Usage: /addsource <name> <adapter> <json>, or send /addsource alone.")
            return
        await create_source(message, app, parts[0], parts[1], parts[2])
        return
    await state.set_state(AddSourceStates.name)
    await message.answer(
        "Send a unique source name (or /cancel).", parse_mode="HTML"
    )


@router.message(AddSourceStates.name)
async def source_name(message: Message, state: FSMContext, app: Application) -> None:
    if not message.text:
        await message.answer("Send the source name as text.")
        return
    name = message.text.strip()
    if not name:
        await message.answer("Source name cannot be empty.")
        return
    if len(name) > 100:
        await message.answer("Source name must be 100 characters or fewer.")
        return
    await state.update_data(source_name=name)
    await state.set_state(AddSourceStates.adapter)
    rows = [
        [InlineKeyboardButton(text=adapter.name, callback_data=f"srcadapter:{adapter.name}")]
        for adapter in app.source_registry.all()
    ]
    await message.answer(
        "Choose a source adapter. Descriptions:\n"
        + "\n".join(f"<code>{esc(item.name)}</code> — {esc(item.description)}" for item in app.source_registry.all()),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(AddSourceStates.adapter, F.data.startswith("srcadapter:"))
async def source_adapter(callback: CallbackQuery, state: FSMContext, app: Application) -> None:
    adapter_name = (callback.data or "").split(":", 1)[1]
    try:
        adapter = app.source_registry.get(adapter_name)
    except SourceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.update_data(adapter=adapter.name)
    await state.set_state(AddSourceStates.config)
    example = json.dumps(adapter.config_example, indent=2, ensure_ascii=False)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"Send the configuration JSON for <code>{esc(adapter.name)}</code>.\n"
            f"Example:\n<pre>{esc(example)}</pre>\nOr /cancel.",
            parse_mode="HTML",
        )


@router.message(AddSourceStates.config)
async def source_config(message: Message, state: FSMContext, app: Application) -> None:
    if not message.text:
        await message.answer("Send the configuration as JSON text.")
        return
    data = await state.get_data()
    name = str(data.get("source_name", ""))
    adapter = str(data.get("adapter", ""))
    try:
        config = parse_config(message.text)
    except SourceError as exc:
        await message.answer(str(exc))
        return
    try:
        source = await app.sources.add(name=name, adapter=adapter, config=config)
    except SourceError as exc:
        await message.answer(f"Could not add source: {esc(exc)}", parse_mode="HTML")
        return
    await state.clear()
    source, result = await app.sources.validate(source.id)
    await message.answer(
        f"Source <b>#{source.id}</b> {esc(source.name)} added with adapter "
        f"<code>{esc(source.adapter)}</code>.\nValidation: <b>{'OK' if result.ok else 'FAILED'}</b> — "
        f"{esc(result.message)}",
        parse_mode="HTML",
    )


@router.message(Command("listsources"))
async def cmd_listsources(message: Message, app: Application) -> None:
    sources = await app.sources.list()
    if not sources:
        await message.answer("No sources yet. Use /addsource.")
        return
    await send_long(message, "<b>Sources</b>\n" + "\n".join(format_source(source) for source in sources))


@router.message(Command("checksource"))
async def cmd_checksource(message: Message, command: object, app: Application) -> None:
    args = getattr(command, "args", None) or ""
    parts = args.split()
    if len(parts) != 1:
        await message.answer("Usage: /checksource <source_id>")
        return
    source_id = await require_int(message, parts[0], "source_id")
    if source_id is None:
        return
    source, result = await app.sources.validate(source_id)
    if source is None:
        await message.answer(f"Source {source_id} was not found.")
        return
    await message.answer(
        f"<b>{esc(source.name)}</b>: {'OK' if result.ok else 'FAILED'} — {esc(result.message)}",
        parse_mode="HTML",
    )
