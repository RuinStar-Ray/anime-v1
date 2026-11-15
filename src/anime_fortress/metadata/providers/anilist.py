from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from anime_fortress.errors import MetadataError, MetadataNotFound
from anime_fortress.metadata.base import (
    AnimeMetadata,
    MetadataCandidate,
    MetadataProvider,
    MetadataResult,
)
from anime_fortress.templates import html_to_plain_text

_SEARCH_QUERY = """
query Search($search: String, $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(search: $search, type: ANIME, isAdult: false, sort: SEARCH_MATCH) {
      id
      title { romaji english native }
      seasonYear
      format
      siteUrl
    }
  }
}
"""

_FETCH_QUERY = """
query Anime($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    title { romaji english native }
    synonyms
    startDate { year month day }
    seasonYear
    episodes
    format
    genres
    studios { nodes { name isAnimationStudio } }
    averageScore
    status
    duration
    description(asHtml: false)
    season
    broadcast { weekday time }
    isAdult
    siteUrl
  }
}
"""

_FORMAT_LABELS = {
    "TV": "TV",
    "TV_SHORT": "TV Short",
    "MOVIE": "Movie",
    "SPECIAL": "Special",
    "OVA": "OVA",
    "ONA": "ONA",
    "MUSIC": "Music",
}

_STATUS_LABELS = {
    "FINISHED": "Finished Airing",
    "RELEASING": "Currently Airing",
    "NOT_YET_RELEASED": "Not yet aired",
    "CANCELLED": "Cancelled",
    "HIATUS": "On Hiatus",
}


class AniListProvider(MetadataProvider):
    name = "anilist"
    display_name = "AniList"
    description = "Public AniList GraphQL API. No API key required."
    requires_credentials = False
    endpoint = "https://graphql.anilist.co"

    async def _post(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "User-Agent": self.runtime.user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        try:
            response = await self.client.post(
                self.endpoint,
                json={"query": query, "variables": variables},
                headers=headers,
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise MetadataError(f"AniList request failed: {exc}") from exc
        if response.status_code == 404:
            raise MetadataNotFound("Anime was not found on AniList")
        if response.status_code == 429:
            raise MetadataError("AniList rate limit reached; try again shortly")
        if response.status_code >= 400:
            raise MetadataError(f"AniList returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise MetadataError("AniList returned invalid JSON") from exc
        if payload.get("errors"):
            raise MetadataError(f"AniList error: {payload['errors'][0].get('message', 'unknown')}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise MetadataError("AniList returned an unexpected response shape")
        return data

    async def search(self, query: str, *, limit: int = 5) -> list[MetadataCandidate]:
        data = await self._post(_SEARCH_QUERY, {"search": query, "perPage": max(1, min(limit, 10))})
        media_items = ((data.get("Page") or {}).get("media")) or []
        candidates: list[MetadataCandidate] = []
        for item in media_items:
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            title = item.get("title") or {}
            candidates.append(
                MetadataCandidate(
                    provider=self.name,
                    external_id=str(item["id"]),
                    title=str(title.get("english") or title.get("romaji") or "Unknown"),
                    year=item.get("seasonYear"),
                    anime_type=_FORMAT_LABELS.get(str(item.get("format")), item.get("format")),
                    source_url=item.get("siteUrl"),
                )
            )
        return candidates

    async def fetch(self, external_id: str) -> MetadataResult:
        try:
            numeric_id = int(external_id)
        except ValueError as exc:
            raise MetadataNotFound(f"AniList ids are numeric, got {external_id!r}") from exc
        data = await self._post(_FETCH_QUERY, {"id": numeric_id})
        media = data.get("Media")
        if not isinstance(media, dict):
            raise MetadataNotFound(f"AniList has no anime with id {external_id}")
        metadata = self._map(media)
        return self._result(
            external_id=str(external_id),
            source_url=media.get("siteUrl"),
            metadata=metadata,
            raw=media,
        )

    def _map(self, media: dict[str, Any]) -> AnimeMetadata:
        titles = media.get("title") or {}
        primary = str(titles.get("english") or titles.get("romaji") or "Unknown")
        native = titles.get("native")
        alternatives = [
            str(item)
            for item in media.get("synonyms") or []
            if item and item not in {primary, native}
        ]
        for value in titles.values():
            if value and value not in {primary, native} and value not in alternatives:
                alternatives.append(str(value))

        start = media.get("startDate") or {}
        release_date: date | None = None
        year = start.get("year") or media.get("seasonYear")
        if start.get("year") and start.get("month") and start.get("day"):
            try:
                release_date = date(int(start["year"]), int(start["month"]), int(start["day"]))
            except ValueError:
                release_date = None

        studios = [
            str(node["name"])
            for node in ((media.get("studios") or {}).get("nodes") or [])
            if isinstance(node, dict) and node.get("name")
        ]
        average_score = media.get("averageScore")
        rating = round(float(average_score) / 10, 1) if isinstance(average_score, (int, float)) else None
        broadcast = media.get("broadcast") or {}
        broadcast_text = None
        if isinstance(broadcast, dict) and (broadcast.get("weekday") or broadcast.get("time")):
            broadcast_text = " ".join(
                str(value).title() if key == "weekday" else str(value)
                for key, value in (("weekday", broadcast.get("weekday")), ("time", broadcast.get("time")))
                if value
            )
        synopsis = media.get("description")
        if isinstance(synopsis, str):
            synopsis = html_to_plain_text(synopsis) or None
        episode_count = media.get("episodes")
        return AnimeMetadata(
            title=primary,
            alternative_titles=list(dict.fromkeys(alternatives)),
            japanese_title=str(native) if native else None,
            release_date=release_date,
            release_year=int(year) if year else None,
            anime_type=_FORMAT_LABELS.get(str(media.get("format")), media.get("format")),
            genres=[str(item) for item in media.get("genres") or []],
            studio=", ".join(studios) or None,
            rating=rating,
            rating_source="AniList",
            status=_STATUS_LABELS.get(str(media.get("status")), media.get("status")),
            duration_minutes=int(media["duration"]) if media.get("duration") else None,
            age_rating="18+" if media.get("isAdult") else None,
            synopsis=synopsis or None,
            broadcast=broadcast_text,
            episode_count=int(episode_count) if episode_count else None,
        )
