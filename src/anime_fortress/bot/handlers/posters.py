from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from uuid import uuid4

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from anime_fortress.app import Application
from anime_fortress.bot.formatting import esc
from anime_fortress.bot.states import ImageUploadStates
from anime_fortress.bot.utils import require_int
from anime_fortress.db.repositories import EpisodeRepository
from anime_fortress.errors import AnimeFortressError

logger = logging.getLogger(__name__)
router = Router(name="images")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
TARGETS = {
    "setposter": ("poster", "anime", "anime poster"),
    "setthumbnail": ("thumbnail", "anime", "anime default thumbnail"),
    "setepthumbnail": ("thumbnail", "episode", "episode thumbnail"),
    "setchannelthumbnail": ("thumbnail", "channel", "channel default thumbnail"),
}


async def target_exists(app: Application, target: str, target_id: int) -> bool:
    if target == "anime":
        return await app.anime.get(target_id) is not None
    if target == "channel":
        return await app.channels.get(target_id) is not None
    async with app.db.session() as session:
        return await EpisodeRepository(session).get(target_id) is not None


async def begin_upload(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    app: Application,
    command_name: str,
) -> None:
    parts = (command.args or "").split()
    usage_target = "anime_id" if command_name != "setchannelthumbnail" else "channel_id"
    if command_name == "setepthumbnail":
        usage_target = "episode_id"
    if len(parts) != 1:
        await message.answer(f"Usage: /{command_name} <{usage_target}>")
        return
    target_id = await require_int(message, parts[0], usage_target)
    if target_id is None:
        return
    kind, target, label = TARGETS[command_name]
    if not await target_exists(app, target, target_id):
        await message.answer(f"{target.capitalize()} {target_id} was not found.")
        return
    await state.set_state(ImageUploadStates.waiting_image)
    await state.update_data(image_kind=kind, image_target=target, image_target_id=target_id)
    await message.answer(
        f"Send the new {label} as a JPEG, PNG, or WebP photo/document (max 20 MB), or /cancel."
    )


@router.message(Command("setposter"))
async def cmd_setposter(
    message: Message, command: CommandObject, state: FSMContext, app: Application
) -> None:
    await begin_upload(message, command, state, app, "setposter")


@router.message(Command("setthumbnail"))
async def cmd_setthumbnail(
    message: Message, command: CommandObject, state: FSMContext, app: Application
) -> None:
    await begin_upload(message, command, state, app, "setthumbnail")


@router.message(Command("setepthumbnail"))
async def cmd_setepthumbnail(
    message: Message, command: CommandObject, state: FSMContext, app: Application
) -> None:
    await begin_upload(message, command, state, app, "setepthumbnail")


@router.message(Command("setchannelthumbnail"))
async def cmd_setchannelthumbnail(
    message: Message, command: CommandObject, state: FSMContext, app: Application
) -> None:
    await begin_upload(message, command, state, app, "setchannelthumbnail")


@router.message(ImageUploadStates.waiting_image, F.photo | F.document)
async def receive_image(message: Message, state: FSMContext, app: Application) -> None:
    file_obj = message.photo[-1] if message.photo else message.document
    if file_obj is None:
        await message.answer("Send the image as a photo or document.")
        return
    if message.document and not (message.document.mime_type or "").startswith("image/"):
        await message.answer("The document must be a JPEG, PNG, or WebP image.")
        return
    size = getattr(file_obj, "file_size", None)
    if size and size > MAX_UPLOAD_BYTES:
        await message.answer("Image exceeds the 20 MB limit.")
        return
    if message.bot is None:
        await message.answer("Telegram bot instance is unavailable; try again.")
        return
    data = await state.get_data()
    kind = str(data.get("image_kind", ""))
    target = str(data.get("image_target", ""))
    target_id = int(data.get("image_target_id", 0))
    buffer = BytesIO()
    try:
        await message.bot.download(file_obj, destination=buffer)
    except Exception as exc:
        logger.warning("Could not download uploaded image: %s", exc)
        await message.answer("Could not download that image from Telegram. Try again.")
        return
    total = buffer.tell()
    if total > MAX_UPLOAD_BYTES:
        await message.answer("Image exceeds the 20 MB limit.")
        return
    buffer.seek(0)
    suffix = ".jpg"
    if message.document and message.document.file_name:
        suffix = "." + message.document.file_name.rsplit(".", 1)[-1].lower() if "." in message.document.file_name else ".jpg"
    temp_path = app.settings.temp_dir / f"upload-{uuid4().hex}{suffix}"
    try:
        await asyncio.to_thread(temp_path.write_bytes, buffer.getvalue())
        original = message.document.file_name if message.document else None
        if kind == "poster":
            await app.posters.import_poster(target_id, temp_path, original_filename=original)
            result = f"Poster stored for anime {target_id}."
        elif target == "anime":
            await app.posters.set_anime_thumbnail(target_id, temp_path, original_filename=original)
            result = f"Default thumbnail stored for anime {target_id}."
        elif target == "episode":
            await app.posters.set_episode_thumbnail(target_id, temp_path, original_filename=original)
            result = f"Thumbnail stored for episode {target_id}."
        elif target == "channel":
            await app.posters.set_channel_thumbnail(target_id, temp_path, original_filename=original)
            result = f"Default thumbnail stored for channel {target_id}."
        else:
            raise AnimeFortressError("Unknown image target")
    except AnimeFortressError as exc:
        await message.answer(f"Could not store image: {esc(exc)}", parse_mode="HTML")
        return
    finally:
        await asyncio.to_thread(temp_path.unlink, missing_ok=True)
        await state.clear()
    await message.answer(result)


@router.message(ImageUploadStates.waiting_image)
async def wrong_image_type(message: Message) -> None:
    await message.answer("Send a JPEG, PNG, or WebP image, or /cancel.")
