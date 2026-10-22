"""Ops error types -- raised when operational commands encounter bad data."""


class OpsError(ValueError):
    """Raised when an ops command finds data it cannot interpret."""
