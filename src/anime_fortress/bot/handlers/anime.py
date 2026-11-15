from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from anime_fortress.app import Application
from anime_fortress.bot.formatting import esc, format_anime, send_long, shorten
from anime_fortress.bot.states import AddAnimeStates
from anime_fortress.bot.utils import require_int, split_list
from anime_fortress.db.models import Episode, Season
from anime_fortress.db.repositories import AnimeRepository, SourceRepository
from anime_fortress.errors import AnimeFortressError

router = Router(name="anime")


def metadata_keyboard(candidates: list[object]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for candidate in candidates:
        provider = getattr(candidate, "provider", "")
        external_id = getattr(candidate, "external_id", "")
        title = str(getattr(candidate, "title", ""))
        year = getattr(candidate, "year", None)
        label = shorten(f"{title} ({year})" if year else title, 55)
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"anp:{provider}:{external_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Manual entry", callback_data="anp:manual:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_sources(message: Message, app: Application, state: FSMContext) -> None:
    sources = await app.anime.sources()
    rows = [
        [InlineKeyboardButton(text=f"#{item.id} {shorten(item.name, 45)}", callback_data=f"ansrc:{item.id}")]
        for item in sources[:12]
    ]
    rows.append([InlineKeyboardButton(text="No source (set later)", callback_data="ansrc:none")])
    await state.set_state(AddAnimeStates.choosing_source)
    await message.answer(
        "Choose the source for this anime.", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


async def show_channels(message: Message, app: Application, state: FSMContext) -> None:
    channels = await app.anime.channels()
    rows = [
        [InlineKeyboardButton(text=f"#{item.id} {shorten(item.title, 45)}", callback_data=f"anchn:{item.id}")]
        for item in channels[:12]
    ]
    rows.append([InlineKeyboardButton(text="No channel (set later)", callback_data="anchn:none")])
    await state.set_state(AddAnimeStates.choosing_channel)
    await message.answer(
        "Choose the destination channel.", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@router.message(Command("addanime"))
async def cmd_addanime(
    message: Message, command: CommandObject, state: FSMContext, app: Application
) -> None:
    args = (command.args or "").strip()
    await state.clear()
    if not args:
        await state.set_state(AddAnimeStates.title)
        await message.answer("Send the anime title to search (or /cancel).")
        return
    await state.update_data(anime_title=args)
    await search_and_offer(message, state, app, args)


async def search_and_offer(
    message: Message, state: FSMContext, app: Application, query: str
) -> None:
    try:
        candidates = await app.metadata.search(query, limit=5)
    except AnimeFortressError as exc:
        await message.answer(f"Metadata search failed: {esc(exc)}", parse_mode="HTML")
        candidates = []
    if not candidates:
        await state.set_state(AddAnimeStates.choosing_metadata)
        await message.answer(
            "No metadata results were found (or providers are unavailable). "
            "Use the button below to add a manual entry.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Manual entry", callback_data="anp:manual:0")]
                ]
            ),
        )
        return
    await state.set_state(AddAnimeStates.choosing_metadata)
    await message.answer(
        "Select the correct metadata entry, or continue manually:",
        reply_markup=metadata_keyboard(candidates),
    )


@router.message(AddAnimeStates.title)
async def anime_title(message: Message, state: FSMContext, app: Application) -> None:
    if not message.text:
        await message.answer("Send the anime title as text.")
        return
    title = message.text.strip()
    if not title:
        await message.answer("Title cannot be empty.")
        return
    await state.update_data(anime_title=title)
    await search_and_offer(message, state, app, title)


@router.callback_query(AddAnimeStates.choosing_metadata, F.data.startswith("anp:"))
async def anime_metadata_choice(
    callback: CallbackQuery, state: FSMContext, app: Application
) -> None:
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer("Invalid selection.", show_alert=True)
        return
    _, provider, external_id = parts
    data = await state.get_data()
    title = str(data.get("anime_title", ""))
    await callback.answer()
    if provider == "manual":
        await state.update_data(metadata_provider=None, metadata_external_id=None)
        if callback.message:
            await show_sources(callback.message, app, state)
        return
    try:
        result = await app.metadata.fetch(provider, external_id)
    except AnimeFortressError as exc:
        if callback.message:
            await callback.message.answer(f"Metadata fetch failed: {esc(exc)}", parse_mode="HTML")
        return
    metadata = result.metadata
    summary = [
        f"<b>{esc(metadata.title or title)}</b>",
        f"Provider: <code>{esc(result.provider)}</code> — id <code>{esc(result.external_id)}</code>",
    ]
    details = []
    if metadata.release_year:
        details.append(str(metadata.release_year))
    if metadata.anime_type:
        details.append(metadata.anime_type)
    if metadata.episode_count:
        details.append(f"{metadata.episode_count} episodes")
    if details:
        summary.append(" — ".join(details))
    if metadata.studio:
        summary.append(f"Studio: {esc(metadata.studio)}")
    if metadata.synopsis:
        summary.append(f"\n{esc(shorten(metadata.synopsis, 500))}")
    await state.update_data(
        metadata_provider=result.provider,
        metadata_external_id=result.external_id,
        metadata_title=metadata.title or title,
    )
    if callback.message:
        await callback.message.answer("\n".join(summary), parse_mode="HTML")
        await show_sources(callback.message, app, state)


@router.callback_query(AddAnimeStates.choosing_source, F.data.startswith("ansrc:"))
async def anime_source_choice(
    callback: CallbackQuery, state: FSMContext, app: Application
) -> None:
    value = (callback.data or "").split(":", 1)[1]
    if value != "none":
        source_id = await require_int(callback.message, value, "source")
        if source_id is None:
            await callback.answer()
            return
        async with app.db.session() as session:
            if await SourceRepository(session).get(source_id) is None:
                await callback.answer("Source was not found.", show_alert=True)
                return
        await state.update_data(source_id=source_id)
    else:
        await state.update_data(source_id=None)
    await callback.answer()
    if callback.message:
        await show_channels(callback.message, app, state)


@router.callback_query(AddAnimeStates.choosing_channel, F.data.startswith("anchn:"))
async def anime_channel_choice(
    callback: CallbackQuery, state: FSMContext, app: Application
) -> None:
    value = (callback.data or "").split(":", 1)[1]
    channel_id = None
    if value != "none":
        channel_id = await require_int(callback.message, value, "channel")
        if channel_id is None:
            await callback.answer()
            return
        if await app.channels.get(channel_id) is None:
            await callback.answer("Channel was not found.", show_alert=True)
            return
    data = await state.get_data()
    title = str(data.get("metadata_title") or data.get("anime_title") or "").strip()
    provider = data.get("metadata_provider")
    external_id = data.get("metadata_external_id")
    source_id = data.get("source_id")
    await callback.answer()
    result = None
    if provider and external_id:
        try:
            result = await app.metadata.fetch(str(provider), str(external_id))
        except AnimeFortressError as exc:
            if callback.message:
                await callback.message.answer(
                    f"Metadata fetch failed: {esc(exc)}. Choose the channel again to retry.",
                    parse_mode="HTML",
                )
            return
    try:
        async with app.db.session() as session:
            anime = await app.metadata.create_anime(
                session,
                title=title,
                source_id=source_id,
                channel_id=channel_id,
                result=result,
            )
            anime_id = anime.id
            final_title = anime.title
    except AnimeFortressError as exc:
        if callback.message:
            await callback.message.answer(f"Could not add anime: {esc(exc)}", parse_mode="HTML")
        return
    await state.clear()
    if callback.message:
        await callback.message.answer(
            f"Anime <b>#{anime_id}</b> {esc(final_title)} created.\n"
            "Next: /setposter, /setlanguages, /setquality, /preview, then /process.",
            parse_mode="HTML",
        )


@router.message(Command("anime"))
async def cmd_anime(message: Message, app: Application) -> None:
    anime = await app.anime.list()
    if not anime:
        await message.answer("No anime yet. Use /addanime.")
        return
    await send_long(message, "<b>Anime</b>\n" + "\n".join(format_anime(item) for item in anime))


@router.message(Command("episodes"))
async def cmd_episodes(
    message: Message, command: CommandObject, app: Application
) -> None:
    parts = (command.args or "").split()
    if len(parts) != 1:
        await message.answer("Usage: /episodes <anime_id>")
        return
    anime_id = await require_int(message, parts[0], "anime_id")
    if anime_id is None:
        return
    async with app.db.session() as session:
        result = await session.execute(
            select(Episode)
            .where(Episode.anime_id == anime_id)
            .options(
                selectinload(Episode.season),
                selectinload(Episode.processed_files),
            )
            .order_by(Episode.season_id, Episode.number)
        )
        episodes = list(result.scalars().all())
        season_numbers = {
            season.id: season.number
            for season in (
                await session.execute(select(Season).where(Season.anime_id == anime_id))
            ).scalars()
        }
    if not episodes:
        await message.answer(f"No episodes discovered for anime {anime_id}.")
        return
    lines = [f"<b>Episodes for anime #{anime_id}</b>"]
    for episode in episodes:
        qualities = ", ".join(
            f"{item.resolution_label}:{item.status.value}" for item in episode.processed_files
        )
        lines.append(
            f"#{episode.id} S{season_numbers.get(episode.season_id, 0):02d}E{episode.number:02d} "
            f"{esc(episode.status.value)}"
            + (f" — {esc(episode.title)}" if episode.title else "")
            + (f" — files: {esc(qualities)}" if qualities else "")
        )
    await send_long(message, "\n".join(lines))


@router.message(Command("setmeta"))
async def cmd_setmeta(
    message: Message, command: CommandObject, app: Application
) -> None:
    parts = (command.args or "").split(maxsplit=2)
    if len(parts) != 3:
        await message.answer(
            "Usage: /setmeta <anime_id> <field> <value>\n"
            "List fields with /help or see docs/metadata.md."
        )
        return
    anime_id = await require_int(message, parts[0], "anime_id")
    if anime_id is None:
        return
    async with app.db.session() as session:
        anime = await AnimeRepository(session).get(anime_id)
        if anime is None:
            await message.answer(f"Anime {anime_id} was not found.")
            return
        try:
            await app.metadata.correct(session, anime, parts[1], parts[2])
        except AnimeFortressError as exc:
            await message.answer(str(exc))
            return
        field = parts[1].strip().lower()
        value = parts[2].strip()
    await message.answer(f"Updated <code>{esc(field)}</code> for anime {anime_id} to: {esc(value)}", parse_mode="HTML")


@router.message(Command("setsource"))
async def cmd_setsource(message: Message, command: CommandObject, app: Application) -> None:
    parts = (command.args or "").split()
    if len(parts) != 2:
        await message.answer("Usage: /setsource <anime_id> <source_id>")
        return
    anime_id = await require_int(message, parts[0], "anime_id")
    source_id = await require_int(message, parts[1], "source_id")
    if anime_id is None or source_id is None:
        return
    try:
        anime = await app.anime.set_source(anime_id, source_id)
    except AnimeFortressError as exc:
        await message.answer(str(exc))
        return
    await message.answer(f"Anime {anime.id} now uses source {source_id}.")


@router.message(Command("setlanguages"))
async def cmd_setlanguages(message: Message, command: CommandObject, app: Application) -> None:
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Usage: /setlanguages <anime_id> <en,ja,...>")
        return
    anime_id = await require_int(message, parts[0], "anime_id")
    if anime_id is None:
        return
    languages = split_list(parts[1])
    try:
        anime = await app.anime.set_languages(anime_id, languages)
    except AnimeFortressError as exc:
        await message.answer(str(exc))
        return
    await message.answer(
        f"Anime {anime.id} preferred audio languages: {', '.join(anime.preferred_languages)}"
    )


@router.message(Command("setquality"))
async def cmd_setquality(message: Message, command: CommandObject, app: Application) -> None:
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Usage: /setquality <anime_id> <1080p,720p,...>")
        return
    anime_id = await require_int(message, parts[0], "anime_id")
    if anime_id is None:
        return
    try:
        anime = await app.anime.set_qualities(anime_id, split_list(parts[1]))
    except AnimeFortressError as exc:
        await message.answer(str(exc))
        return
    await message.answer(
        f"Anime {anime.id} preferred qualities: {', '.join(anime.preferred_qualities)}"
    )


@router.message(Command("setextraaudio"))
async def cmd_setextraaudio(message: Message, command: CommandObject, app: Application) -> None:
    parts = (command.args or "").split()
    if len(parts) != 2 or parts[1].lower() not in {"on", "off"}:
        await message.answer("Usage: /setextraaudio <anime_id> <on|off>")
        return
    anime_id = await require_int(message, parts[0], "anime_id")
    if anime_id is None:
        return
    try:
        anime = await app.anime.set_extra_audio(anime_id, parts[1].lower() == "on")
    except AnimeFortressError as exc:
        await message.answer(str(exc))
        return
    await message.answer(
        f"Anime {anime.id} extra/commentary audio: {'included' if anime.include_extra_audio else 'excluded'}."
    )
