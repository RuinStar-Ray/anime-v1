from __future__ import annotations

import argparse
import asyncio
import shutil
import signal
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect, text

from alembic import command as alembic_command
from anime_fortress.app import Application
from anime_fortress.bot.app import build_dispatcher, set_bot_commands
from anime_fortress.config import Settings
from anime_fortress.db.session import create_database
from anime_fortress.errors import ConfigurationError
from anime_fortress.logging_setup import configure_logging
from anime_fortress.metadata.registry import default_metadata_registry
from anime_fortress.services.bootstrap import bootstrap_defaults
from anime_fortress.services.worker import Worker
from anime_fortress.sources.registry import default_source_registry
from anime_fortress.telegram.sender import AiogramSender

EXPECTED_TABLES = {
    "sources",
    "channels",
    "templates",
    "posters",
    "anime",
    "seasons",
    "episodes",
    "source_media",
    "processed_files",
    "audio_tracks",
    "queue_tasks",
    "upload_records",
    "announcements",
    "settings",
    "audit_log",
}


def project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "alembic.ini").exists():
            return parent
    raise ConfigurationError(
        "alembic.ini was not found. Run anime-fortress from the repository or Docker image."
    )


def alembic_config(settings: Settings) -> AlembicConfig:
    root = project_root()
    config = AlembicConfig(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    config.attributes["settings"] = settings
    return config


def upgrade_schema(settings: Settings) -> None:
    alembic_command.upgrade(alembic_config(settings), "head")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anime-fortress",
        description="Automated anime publishing pipeline for Telegram channels",
    )
    parser.add_argument("--version", action="version", version="anime-fortress 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-db", help="Apply migrations and seed defaults")
    init_parser.set_defaults(handler="init-db")

    migrate_parser = subparsers.add_parser("migrate", help="Apply database migrations")
    migrate_parser.set_defaults(handler="migrate")

    doctor_parser = subparsers.add_parser("doctor", help="Validate configuration and environment")
    doctor_parser.set_defaults(handler="doctor")

    bot_parser = subparsers.add_parser("bot", help="Run the admin bot and worker")
    bot_parser.add_argument("--no-migrate", action="store_true", help="Skip automatic migrations")
    bot_parser.set_defaults(handler="bot")

    worker_parser = subparsers.add_parser("worker", help="Run the queue worker only")
    worker_parser.add_argument("--no-migrate", action="store_true", help="Skip automatic migrations")
    worker_parser.set_defaults(handler="worker")

    return parser


def create_bot(settings: Settings) -> Bot:
    token = settings.require_bot_token().get_secret_value()
    return Bot(token=token, default=DefaultBotProperties(parse_mode=None))


async def run_init_db(settings: Settings) -> int:
    settings.ensure_directories()
    database = create_database(settings)
    try:
        async with database.session() as session:
            await bootstrap_defaults(session, settings)
        print(f"Database ready: {settings.database_url.split('://', 1)[0]} ({settings.data_dir})")
        return 0
    finally:
        await database.dispose()


async def check_tool(path: str, label: str) -> tuple[bool, str]:
    executable = path if Path(path).is_absolute() else shutil.which(path)
    if not executable:
        return False, f"{label} not found (configured: {path})"
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            [str(executable), "-version"],
            capture_output=True,
            timeout=20,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        return False, f"{label} could not run: {exc}"
    if completed.returncode != 0:
        return False, f"{label} exited with code {completed.returncode}"
    first_line = completed.stdout.decode("utf-8", "replace").splitlines()
    return True, first_line[0] if first_line else f"{label} available"


async def run_doctor(settings: Settings) -> int:
    failures = 0
    warnings = 0
    print(f"Anime Fortress doctor ({settings.app_env})")

    try:
        settings.ensure_directories()
        probe = settings.data_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        print(f"[OK] directories writable: {settings.data_dir}")
    except Exception as exc:
        failures += 1
        print(f"[FAIL] directories are not writable: {exc}")

    database = create_database(settings)
    try:
        async with database.session() as session:
            await session.execute(text("SELECT 1"))
        async with database.engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            version = None
            if "alembic_version" in tables:
                version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        missing = EXPECTED_TABLES - tables
        if missing:
            failures += 1
            print(f"[FAIL] database schema missing tables: {', '.join(sorted(missing))}")
            print("       Run: anime-fortress init-db")
        else:
            print(f"[OK] database schema present (revision: {version or 'unknown'})")
    except Exception as exc:
        failures += 1
        print(f"[FAIL] database unavailable: {exc}")
    finally:
        await database.dispose()

    ok, description = await check_tool(settings.ffmpeg_path, "ffmpeg")
    print(f"[{'OK' if ok else 'FAIL'}] {description}")
    failures += 0 if ok else 1
    ok, description = await check_tool(settings.ffprobe_path, "ffprobe")
    print(f"[{'OK' if ok else 'FAIL'}] {description}")
    failures += 0 if ok else 1

    adapters = default_source_registry(load_plugins=True).names()
    print(f"[OK] source adapters: {', '.join(adapters)}")
    registry, client, owned = default_metadata_registry(settings)
    try:
        names = [provider.name for provider in registry.ordered()]
        print(f"[OK] metadata providers: {', '.join(names)}")
    finally:
        if owned:
            await client.aclose()

    if settings.bot_token and settings.bot_token.get_secret_value().strip():
        print("[OK] BOT_TOKEN configured")
    else:
        warnings += 1
        print("[WARN] BOT_TOKEN is not configured; bot and worker commands cannot run")
    if settings.admin_user_ids:
        print(f"[OK] admin user IDs: {', '.join(str(item) for item in settings.admin_user_ids)}")
    else:
        warnings += 1
        print("[WARN] ADMIN_USER_IDS is empty; nobody can use the admin bot")
    if not settings.media_library_roots:
        warnings += 1
        print("[WARN] MEDIA_LIBRARY_ROOTS is empty; local_library sources will reject paths")
    if settings.require_announcement:
        print("[OK] announcement requirement enabled")
    print(
        f"Summary: {failures} failure(s), {warnings} warning(s). "
        + ("Environment is not ready." if failures else "Ready to run.")
    )
    return 1 if failures else 0


async def run_bot(settings: Settings) -> int:
    bot = create_bot(settings)
    sender = AiogramSender(bot)
    app: Application | None = None
    worker: Worker | None = None
    try:
        app = await Application.create(settings, sender)
        await app.bootstrap()
        worker = Worker(app.db, settings, app.media, app.publish)
        dispatcher = build_dispatcher(app)
        await set_bot_commands(bot, app)
        await worker.start()
        print("Anime Fortress bot and worker are running. Press Ctrl+C to stop.")
        await dispatcher.start_polling(bot, close_bot_session=False)
        return 0
    finally:
        if worker is not None:
            await worker.stop()
        if app is not None:
            await app.close()
        await bot.session.close()


async def run_worker(settings: Settings) -> int:
    bot = create_bot(settings)
    sender = AiogramSender(bot)
    app: Application | None = None
    worker: Worker | None = None
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass
    try:
        app = await Application.create(settings, sender)
        await app.bootstrap()
        worker = Worker(app.db, settings, app.media, app.publish)
        await worker.start()
        print("Anime Fortress worker is running. Press Ctrl+C to stop.")
        try:
            await stop_event.wait()
        except KeyboardInterrupt:
            pass
        return 0
    finally:
        if worker is not None:
            await worker.stop()
        if app is not None:
            await app.close()
        await bot.session.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = Settings()
        if args.handler in {"bot", "worker"} and not args.no_migrate:
            upgrade_schema(settings)
        elif args.handler in {"init-db", "migrate"}:
            upgrade_schema(settings)
        configure_logging(settings.log_level, settings.log_json)
        if args.handler == "migrate":
            print("Database migrations applied.")
            return 0
        if args.handler == "init-db":
            return asyncio.run(run_init_db(settings))
        if args.handler == "doctor":
            return asyncio.run(run_doctor(settings))
        if args.handler == "bot":
            return asyncio.run(run_bot(settings))
        if args.handler == "worker":
            return asyncio.run(run_worker(settings))
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
