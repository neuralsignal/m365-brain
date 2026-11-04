"""Custom exceptions for the index package."""


class QueryError(ValueError):
    """Raised when a search query or filter expression cannot be parsed."""


class SearchError(Exception):
    """Raised when a search request is invalid or cannot be executed."""


class VectorStoreError(Exception):
    """Raised when a vector store operation fails due to mismatched dimensions or counts."""


class IndexConfigError(Exception):
    """Raised when an index configuration value is invalid at runtime."""


class CatalogError(Exception):
    """Raised when a catalog operation references an unknown state."""
