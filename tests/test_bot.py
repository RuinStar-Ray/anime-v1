from __future__ import annotations

from types import SimpleNamespace

from anime_fortress.bot.app import ADMIN_COMMANDS, PUBLIC_COMMANDS, build_dispatcher
from anime_fortress.bot.formatting import esc, format_task, shorten
from anime_fortress.bot.guards import IsAdmin, IsPrivateAdminChat
from anime_fortress.config import Settings
from anime_fortress.db.enums import QueueStatus, TaskKind
from anime_fortress.db.models import QueueTask


async def test_admin_filters(settings: Settings) -> None:
    admin = SimpleNamespace(from_user=SimpleNamespace(id=101), chat=SimpleNamespace(type="private"))
    stranger = SimpleNamespace(from_user=SimpleNamespace(id=999), chat=SimpleNamespace(type="private"))
    group = SimpleNamespace(from_user=SimpleNamespace(id=101), chat=SimpleNamespace(type="group"))
    assert await IsAdmin()(admin, settings=settings) is True
    assert await IsAdmin()(stranger, settings=settings) is False
    assert await IsPrivateAdminChat()(admin, settings=settings) is True
    assert await IsPrivateAdminChat()(group, settings=settings) is False


async def test_build_dispatcher_and_commands(settings: Settings) -> None:
    app = SimpleNamespace(settings=settings)
    dispatcher = build_dispatcher(app)
    assert dispatcher["settings"] is settings
    public_names = {command.command for command in PUBLIC_COMMANDS}
    admin_names = {command.command for command in ADMIN_COMMANDS}
    assert public_names <= admin_names
    assert {"start", "help", "addanime", "status", "process", "pause", "retry"} <= admin_names
    assert "setposter" in admin_names


def test_formatting_helpers() -> None:
    assert esc("<b>&") == "&lt;b&gt;&amp;"
    assert shorten("abcdef", 4) == "abc\u2026"
    task = QueueTask(
        id=7,
        kind=TaskKind.PROCESS_EPISODE,
        status=QueueStatus.RUNNING,
        stage="downloading",
        progress=42,
        attempts=2,
        max_attempts=5,
        last_error=None,
    )
    rendered = format_task(task)
    assert "#7" in rendered
    assert "42%" in rendered
    assert "attempt 2/5" in rendered
    assert "downloading" in rendered
