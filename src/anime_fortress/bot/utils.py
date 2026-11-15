from __future__ import annotations

import re

from aiogram.types import Message


async def require_int(message: Message, raw: str, label: str) -> int | None:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        await message.answer(f"{label} must be an integer, got '{raw}'.")
        return None
    if value <= 0:
        await message.answer(f"{label} must be a positive integer.")
        return None
    return value


def split_list(raw: str) -> list[str]:
    return [part for part in re.split(r"[,\s]+", raw.strip()) if part]
