from __future__ import annotations

import html
import re
import string
from collections.abc import Mapping
from typing import Any

from anime_fortress.errors import TemplateError

NUMERIC_FIELDS = {"season", "episode", "release_year", "episode_count", "seasons", "year"}

TEMPLATE_FIELDS = {
    "anime_name",
    "anime_title",
    "alternative_titles",
    "japanese_title",
    "release_date",
    "release_year",
    "seasons",
    "episode_count",
    "type",
    "genres",
    "studio",
    "rating",
    "rating_source",
    "status",
    "duration",
    "age_rating",
    "synopsis",
    "broadcast",
    "audio_languages",
    "available_qualities",
    "branding",
    "channel_name",
    "file_name",
    "quality",
    "audio_label",
    "season",
    "episode",
    "year",
}
CAPTION_FIELDS = TEMPLATE_FIELDS
_HTML_BLOCK_RE = re.compile(r"(?i)</?(?:p|div|blockquote|pre|h[1-6]|ul|ol|li)[^>]*>")
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_TRAILING_WS_RE = re.compile(r"[ \t]+$", re.MULTILINE)


class TemplateEngine:
    def __init__(
        self,
        *,
        missing_value: str = "N/A",
        cleanup_blank_lines: bool = True,
        max_caption_length: int = 1024,
    ) -> None:
        self.missing_value = missing_value
        self.cleanup_blank_lines = cleanup_blank_lines
        self.max_caption_length = max_caption_length

    def allowed_fields(self, fields: set[str] | None = None) -> set[str]:
        return set(fields or TEMPLATE_FIELDS)

    def validate(self, body: str, *, fields: set[str] | None = None) -> None:
        if not body or not body.strip():
            raise TemplateError("Template cannot be empty")
        allowed = self.allowed_fields(fields)
        try:
            parsed = list(string.Formatter().parse(body))
        except ValueError as exc:
            raise TemplateError(f"Invalid template syntax: {exc}") from exc
        unknown: set[str] = set()
        for _, field_name, _, _ in parsed:
            if field_name is None:
                continue
            root = field_name.split(".", 1)[0].split("[", 1)[0]
            if root and root not in allowed:
                unknown.add(root)
        if unknown:
            raise TemplateError(
                "Unknown template placeholder(s): "
                + ", ".join(sorted(f"{{{name}}}" for name in unknown))
                + ". Allowed: "
                + ", ".join(sorted(allowed))
            )

    def render(
        self,
        body: str,
        context: Mapping[str, Any],
        *,
        escape_html: bool = True,
        validate: bool = True,
    ) -> str:
        if validate:
            self.validate(body)
        output: list[str] = []
        try:
            parsed = list(string.Formatter().parse(body))
        except ValueError as exc:
            raise TemplateError(f"Invalid template syntax: {exc}") from exc
        for literal, field_name, format_spec, conversion in parsed:
            if literal:
                output.append(literal)
            if field_name is None:
                continue
            root = field_name.split(".", 1)[0].split("[", 1)[0]
            value = context.get(root)
            value = self._coerce_value(value)
            if conversion == "r":
                value = repr(value)
            elif conversion == "a":
                value = ascii(value)
            elif conversion == "s":
                value = str(value)
            if isinstance(value, int) and root in NUMERIC_FIELDS:
                try:
                    formatted = format(value, format_spec) if format_spec else str(value)
                except ValueError as exc:
                    raise TemplateError(f"Bad format for {{{root}}}: {exc}") from exc
            else:
                text = str(value)
                try:
                    formatted = format(text, format_spec) if format_spec else text
                except ValueError as exc:
                    raise TemplateError(f"Bad format for {{{root}}}: {exc}") from exc
            if escape_html:
                formatted = html.escape(formatted, quote=False)
            output.append(formatted)
        rendered = "".join(output)
        if self.cleanup_blank_lines:
            rendered = _TRAILING_WS_RE.sub("", rendered)
            rendered = _BLANK_LINES_RE.sub("\n\n", rendered).strip()
        return rendered

    def render_caption(self, body: str, context: Mapping[str, Any]) -> str:
        rendered = self.render(body, context, escape_html=True)
        if len(rendered) > self.max_caption_length:
            raise TemplateError(
                f"Rendered caption is {len(rendered)} characters; Telegram allows "
                f"{self.max_caption_length}. Shorten the caption template or filenames."
            )
        return rendered

    def _coerce_value(self, value: Any) -> Any:
        if value is None:
            return self.missing_value
        if isinstance(value, str):
            return value.strip() if value.strip() else self.missing_value
        if isinstance(value, (list, tuple, set)):
            items = [str(item).strip() for item in value if str(item).strip()]
            return ", ".join(items) if items else self.missing_value
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return value


def html_to_plain_text(text: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:p|div|blockquote|h[1-6]|li)>", "\n", text)
    text = _HTML_BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_plain_text(text: str, limit: int) -> list[str]:
    if limit < 64:
        raise ValueError("Message limit is too small")
    if len(text) <= limit:
        return [text]
    prefix_reserve = len("[9999/9999]\n")
    content_limit = max(1, limit - prefix_reserve)
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= content_limit:
            chunks.append(remaining)
            break
        window = remaining[:content_limit]
        split_at = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(". "))
        if split_at < content_limit // 3:
            split_at = window.rfind(" ")
        if split_at < content_limit // 3:
            split_at = content_limit
        chunk = remaining[:split_at].rstrip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].lstrip("\n ")
    if len(chunks) > 1:
        total = len(chunks)
        prefixed = []
        for index, chunk in enumerate(chunks, start=1):
            item = f"[{index}/{total}]\n{chunk}"
            prefixed.append(item if len(item) <= limit else chunk)
        chunks = prefixed
    return chunks


def split_announcement_messages(text: str, limit: int = 4096) -> list[str]:
    """Return Telegram-safe messages. Oversized formatted text falls back to plain text chunks."""
    if len(text) <= limit:
        return [text]
    return split_plain_text(html_to_plain_text(text), limit)
