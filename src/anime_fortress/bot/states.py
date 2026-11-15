from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AddSourceStates(StatesGroup):
    name = State()
    adapter = State()
    config = State()


class AddAnimeStates(StatesGroup):
    title = State()
    choosing_metadata = State()
    choosing_source = State()
    choosing_channel = State()


class ImageUploadStates(StatesGroup):
    waiting_image = State()


class TemplateStates(StatesGroup):
    waiting_body = State()
