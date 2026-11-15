from __future__ import annotations

PRIORITY_PROCESS_EPISODE = 100
PRIORITY_ANNOUNCEMENT = 500
PRIORITY_PUBLISH_BASE = 1000
_QUALITY_RANK = {
    "240p": 0,
    "360p": 1,
    "480p": 2,
    "540p": 3,
    "576p": 4,
    "720p": 5,
    "1080p": 6,
    "1440p": 7,
    "2160p": 8,
    "4320p": 9,
}


def publish_priority(season_number: int, episode_number: int, quality: str) -> int:
    rank = _QUALITY_RANK.get(quality.strip().lower(), 50)
    return (
        PRIORITY_PUBLISH_BASE
        + max(season_number, 0) * 100000
        + max(episode_number, 0) * 100
        + rank
    )
