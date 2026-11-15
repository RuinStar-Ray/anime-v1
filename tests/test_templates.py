from __future__ import annotations

import pytest

from anime_fortress.config import DEFAULT_ANNOUNCEMENT_TEMPLATE
from anime_fortress.errors import TemplateError
from anime_fortress.templates import TemplateEngine, split_announcement_messages


@pytest.fixture
def engine() -> TemplateEngine:
    return TemplateEngine(missing_value="N/A", max_caption_length=100)


def test_render_escapes_html_and_handles_missing(engine: TemplateEngine) -> None:
    body = "<b>{anime_title}</b> {rating} {seasons}"
    rendered = engine.render(body, {"anime_title": "A & B <Tag>", "rating": "8.5"})
    assert rendered == "<b>A &amp; B &lt;Tag&gt;</b> 8.5 N/A"


def test_render_supports_numeric_format_and_cleanup(engine: TemplateEngine) -> None:
    rendered = engine.render(
        "{season:02d} {episode:03d}\n\n\n\n{genres}",
        {"season": 1, "episode": 7, "genres": ["Action", "Drama"]},
    )
    assert rendered == "01 007\n\nAction, Drama"


def test_render_rejects_unknown_placeholders(engine: TemplateEngine) -> None:
    with pytest.raises(TemplateError):
        engine.render("{secret}", {"anime_title": "x"})


def test_render_caption_enforces_limit(engine: TemplateEngine) -> None:
    with pytest.raises(TemplateError):
        engine.render_caption("{synopsis}", {"synopsis": "x" * 101})


def test_render_handles_bad_format_spec(engine: TemplateEngine) -> None:
    with pytest.raises(TemplateError):
        engine.render("{season:bad}", {"season": 1})


def test_default_announcement_template_is_valid(engine: TemplateEngine) -> None:
    engine.validate(DEFAULT_ANNOUNCEMENT_TEMPLATE)


def test_split_announcement_messages_preserves_text() -> None:
    text = "\n".join(f"line {index}" for index in range(20))
    chunks = split_announcement_messages(text, 80)
    assert len(chunks) > 1
    assert all(len(chunk) <= 80 for chunk in chunks)
    assert "line 0" in chunks[0]
    assert "line 19" in "".join(chunks)
