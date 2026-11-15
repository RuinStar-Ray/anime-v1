from __future__ import annotations


class AnimeFortressError(Exception):
    """Base class for expected, user-actionable application errors."""


class ConfigurationError(AnimeFortressError):
    pass


class ValidationError(AnimeFortressError):
    pass


class SourceError(AnimeFortressError):
    pass


class SourceNotSupported(SourceError):
    pass


class SourceAuthRequired(SourceError):
    pass


class MetadataError(AnimeFortressError):
    pass


class MetadataNotFound(MetadataError):
    pass


class MediaError(AnimeFortressError):
    pass


class MediaInspectionError(MediaError):
    pass


class MediaProcessingError(MediaError):
    pass


class UploadTooLarge(MediaError):
    pass


class TemplateError(AnimeFortressError):
    pass


class QueueError(AnimeFortressError):
    pass


class RetryableTaskError(AnimeFortressError):
    """A task failed for a transient reason and should be retried later."""

    def __init__(self, message: str, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class PermanentTaskError(AnimeFortressError):
    """A task cannot succeed without an administrator changing something."""


class TelegramPublishError(AnimeFortressError):
    pass


class NotAuthorizedError(AnimeFortressError):
    pass
