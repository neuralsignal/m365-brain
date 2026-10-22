"""Index error types -- raised when index operations encounter invalid input."""


class IndexConfigError(ValueError):
    """Raised when an index configuration value is invalid or contradictory."""


class QueryError(ValueError):
    """Raised when a search query or filter expression cannot be parsed."""


class VectorError(ValueError):
    """Raised when vector-store operations detect shape or dimension mismatches."""
