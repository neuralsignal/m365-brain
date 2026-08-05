"""Extractor error types."""


class ExtractorError(Exception):
    """Raised when an extractor encounters a non-Graph error."""


class MessageStoreError(ExtractorError):
    """Raised when a per-conversation message store file is corrupt or unreadable."""
