"""Config error type — raised when config loading or validation fails."""


class ConfigError(ValueError):
    """Raised when config loading or validation fails.

    Inherits from ``ValueError`` so that Pydantic model/field validators can
    raise it and have it collected into ``ValidationError`` like a plain
    ``ValueError``.
    """
