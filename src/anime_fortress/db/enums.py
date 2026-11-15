from __future__ import annotations

from enum import StrEnum


class SourceStatus(StrEnum):
    UNVERIFIED = "unverified"
    VALID = "valid"
    INVALID = "invalid"
    DISABLED = "disabled"


class TemplateKind(StrEnum):
    ANNOUNCEMENT = "announcement"
    CAPTION = "caption"


class EpisodeStatus(StrEnum):
    DISCOVERED = "discovered"
    PROCESSING = "processing"
    PROCESSED = "processed"
    QUEUED = "queued"
    UPLOADED = "uploaded"
    FAILED = "failed"
    SKIPPED = "skipped"


class MediaStatus(StrEnum):
    DISCOVERED = "discovered"
    DOWNLOADED = "downloaded"
    FAILED = "failed"


class ProcessedStatus(StrEnum):
    READY = "ready"
    QUEUED = "queued"
    UPLOADED = "uploaded"
    FAILED = "failed"


class QueueStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"
    CANCELLED = "cancelled"


class TaskKind(StrEnum):
    PUBLISH_ANNOUNCEMENT = "publish_announcement"
    PROCESS_EPISODE = "process_episode"
    PUBLISH_EPISODE = "publish_episode"


class UploadStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


ACTIVE_QUEUE_STATUSES = (QueueStatus.QUEUED, QueueStatus.RUNNING)
