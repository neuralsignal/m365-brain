"""Graph transport exceptions.

Their own module for one reason: ``graph_helpers`` needs to raise
``GraphApiError`` from the download-URL guard, and ``client`` needs to import
``graph_helpers``. A shared leaf breaks the cycle that putting the exceptions in
either of those two would create.

``client`` re-exports all three, so ``from m365_brain.m365.client import
GraphApiError`` -- which every extractor writes -- keeps working. The
subclasses are new; nothing that catches ``GraphApiError`` had to change to
accommodate them, which is the whole reason they subclass it.
"""

from __future__ import annotations


class GraphApiError(Exception):
    """Raised when a Graph API request fails after exhausting retries.

    ``status_code`` carries the HTTP status when the failure came from an HTTP
    response (``None`` for logical/transport-level failures).
    """

    def __init__(self, message: str, status_code: int | None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GraphNotFoundError(GraphApiError):
    """HTTP 404 -- the resource is gone, and retrying will not bring it back.

    Callers that must tell "deleted" from "transient" catch this; everyone else
    keeps catching ``GraphApiError`` and reading ``exc.status_code``.
    """


class GraphConflictError(GraphApiError):
    """HTTP 412 -- an ``If-Match`` eTag no longer matches the remote item.

    The remote changed since it was read. Raised before the retry branch, so a
    conditional write never silently becomes an unconditional one.
    """
