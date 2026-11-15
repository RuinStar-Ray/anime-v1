from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from anime_fortress.config import Settings, split_csv, split_paths


def test_split_csv_handles_comma_and_json() -> None:
    assert split_csv("a, b,,c") == ["a", "b", "c"]
    assert split_csv('["x", " y ", ""]') == ["x", "y"]
    assert split_csv(None) == []


def test_split_paths_handles_windows_separator() -> None:
    assert len(split_paths("C:/one;D:/two")) == 2
    assert split_paths([]) == []


def test_settings_defaults_and_directory_resolution(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.database_url.startswith("sqlite+aiosqlite:///")
    assert settings.temp_dir == (tmp_path / "tmp").resolve()
    assert settings.media_dir == (tmp_path / "media").resolve()
    assert settings.max_upload_bytes == int(settings.max_upload_mb * 1024 * 1024)
    assert settings.is_admin(123) is False
    settings.ensure_directories()
    assert settings.temp_dir.is_dir()
    assert settings.media_dir.is_dir()


def test_settings_parses_admin_ids_and_quality_preferences() -> None:
    settings = Settings(
        _env_file=None,
        admin_user_ids="42, 7,42",
        preferred_qualities="1080P, 720p,1080p",
        preferred_languages="Hindi, English, hindi",
    )
    assert settings.admin_user_ids == [7, 42]
    assert settings.preferred_qualities == ["1080p", "720p"]
    assert settings.preferred_languages == ["Hindi", "English"]
    assert settings.is_admin(42)


def test_settings_rejects_invalid_values() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, admin_user_ids="not-an-id")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, worker_concurrency=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, timezone="Mars/Olympus")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, audio_label_style="fancy")


def test_require_bot_token() -> None:
    with pytest.raises(RuntimeError):
        Settings(_env_file=None, bot_token=None).require_bot_token()
    token = Settings(_env_file=None, bot_token="123:abc").require_bot_token()
    assert token.get_secret_value() == "123:abc"
