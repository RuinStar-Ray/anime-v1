from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import httpx

from anime_fortress.errors import MetadataError, MetadataNotFound
from anime_fortress.metadata.base import (
    AnimeMetadata,
    MetadataCandidate,
    MetadataProvider,
    MetadataResult,
)

_DURATION_RE = re.compile(r"(\d+)\s*min", re.IGNORECASE)


class JikanProvider(MetadataProvider):
    name = "jikan"
    display_name = "MyAnimeList (Jikan v4)"
    description = "Public Jikan v4 API for MyAnimeList metadata. No API key required."
    requires_credentials = False
    base_url = "https://api.jikan.moe/v4"

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"User-Agent": self.runtime.user_agent, "Accept": "application/json"}
        try:
            response = await self.client.get(
                f"{self.base_url}{path}", params=params, headers=headers, timeout=30
            )
        except httpx.HTTPError as exc:
            raise MetadataError(f"Jikan request failed: {exc}") from exc
        if response.status_code == 404:
            raise MetadataNotFound("Anime was not found on MyAnimeList/Jikan")
        if response.status_code == 429:
            raise MetadataError("Jikan rate limit reached; try again shortly")
        if response.status_code >= 400:
            raise MetadataError(f"Jikan returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise MetadataError("Jikan returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise MetadataError("Jikan returned an unexpected response shape")
        return payload

    async def search(self, query: str, *, limit: int = 5) -> list[MetadataCandidate]:
        payload = await self._get(
            "/anime", params={"q": query, "limit": max(1, min(limit, 10)), "sfw": "true"}
        )
        candidates: list[MetadataCandidate] = []
        for item in payload.get("data") or []:
            if not isinstance(item, dict) or item.get("mal_id") is None:
                continue
            candidates.append(
                MetadataCandidate(
                    provider=self.name,
                    external_id=str(item["mal_id"]),
                    title=str(item.get("title_english") or item.get("title") or "Unknown"),
                    year=item.get("year"),
                    anime_type=item.get("type"),
                    source_url=item.get("url"),
                )
            )
        return candidates

    async def fetch(self, external_id: str) -> MetadataResult:
        payload = await self._get(f"/anime/{external_id}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise MetadataNotFound(f"Jikan has no anime with id {external_id}")
        metadata = self._map(data)
        return self._result(
            external_id=str(external_id),
            source_url=data.get("url"),
            metadata=metadata,
            raw=data,
        )

    def _map(self, data: dict[str, Any]) -> AnimeMetadata:
        primary = str(data.get("title_english") or data.get("title") or "Unknown")
        japanese = data.get("title_japanese")
        alternatives: list[str] = []
        for item in data.get("titles") or []:
            title = item.get("title") if isinstance(item, dict) else None
            if title and title not in {primary, japanese}:
                alternatives.append(str(title))
        for synonym in data.get("title_synonyms") or []:
            if synonym and synonym not in {primary, japanese}:
                alternatives.append(str(synonym))
        alternatives = list(dict.fromkeys(alternatives))

        aired = data.get("aired") or {}
        release_date: date | None = None
        raw_from = aired.get("from") if isinstance(aired, dict) else None
        if isinstance(raw_from, str):
            try:
                release_date = datetime.fromisoformat(raw_from.replace("Z", "+00:00")).date()
            except ValueError:
                release_date = None
        release_year = data.get("year")
        if release_year is None and isinstance(aired, dict):
            release_year = (aired.get("prop") or {}).get("from", {}).get("year")

        duration_minutes = None
        duration_text = data.get("duration")
        if isinstance(duration_text, str):
            match = _DURATION_RE.search(duration_text)
            if match:
                duration_minutes = int(match.group(1))

        genres = [
            str(item["name"])
            for item in data.get("genres") or []
            if isinstance(item, dict) and item.get("name")
        ]
        studios = [
            str(item["name"])
            for item in data.get("studios") or []
            if isinstance(item, dict) and item.get("name")
        ]
        score = data.get("score")
        rating = float(score) if isinstance(score, (int, float)) else None

        return AnimeMetadata(
            title=primary,
            alternative_titles=alternatives,
            japanese_title=str(japanese) if japanese else None,
            release_date=release_date,
            release_year=int(release_year) if release_year else None,
            anime_type=str(data["type"]) if data.get("type") else None,
            genres=genres,
            studio=", ".join(studios) or None,
            rating=rating,
            rating_source="MyAnimeList",
            status=str(data["status"]) if data.get("status") else None,
            duration_minutes=duration_minutes,
            age_rating=str(data["rating"]) if data.get("rating") else None,
            synopsis=str(data["synopsis"]) if data.get("synopsis") else None,
            broadcast=str(data["broadcast"]["string"])
            if isinstance(data.get("broadcast"), dict) and data["broadcast"].get("string")
            else None,
            episode_count=int(data["episodes"]) if data.get("episodes") else None,
        )
