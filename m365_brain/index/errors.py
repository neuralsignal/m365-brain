"""Exceptions for the index subsystem."""


class VectorStoreError(Exception):
    """Raised when a vector store operation fails due to dimension or count mismatch."""


class QueryError(Exception):
    """Raised when a search query cannot be parsed."""


class SearchError(Exception):
    """Raised when a search request is invalid."""


class CatalogConversionError(Exception):
    """A conversion that failed for a reason worth recording on the row."""
