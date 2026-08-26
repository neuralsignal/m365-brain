"""Graph transport exceptions.

Their own module for one reason: ``graph_helpers`` needs to raise
``GraphApiError`` from the download-URL guard, and ``client`` needs to import
``graph_helpers``. A shared leaf breaks the cycle that putting the exceptions in
either of those two would create.

``client`` re-exports the three Graph errors, so ``from
m365_brain.m365.client import GraphApiError`` -- which every extractor writes
-- keeps working. The subclasses are new; nothing that catches
``GraphApiError`` had to change to accommodate them, which is the whole reason
they subclass it.

``AuthTransportError`` lives here for the same leaf reason and for the
opposite inheritance reason: ``client`` catches it, ``m365/auth/`` raises it,
and it is pointedly *not* a ``GraphApiError``. Its own docstring says why.
``TokenCacheError`` is here because it is raised from the same place and is
*not* an ``OSError``, which is likewise the whole point of it.
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


class AuthTransportError(Exception):
    """A network-level failure while acquiring or refreshing a token.

    MSAL reaches the identity provider over ``requests``; the extractors reach
    Graph over ``httpx``. One DNS failure therefore surfaces as two unrelated
    exception types depending only on which of the two calls happened to be in
    flight -- and just the ``httpx`` one sat inside ``GraphClient``'s retry
    envelope. Observed 2026-08-25 on a laptop wake: a cycle that faulted on a
    data call retried three times and recovered, while the same fault twenty
    minutes later on a *token* call killed all five extractors outright. The
    auth adapter translates the ``requests`` type into this one at the edge, so
    the retry loop has a single domain exception to catch and never learns that
    ``requests`` exists.

    ``transient = True`` is a **contract read from outside this package half**.
    ``outbox/runner.py`` may not import this module -- ``outbox`` and ``m365``
    are peers -- so it duck-types on this attribute to decide whether a failed
    dispatch is put back or archived as permanently failed, exactly as
    ``_classify_failure`` already duck-types on ``status_code``. Removing the
    attribute or setting it False silently makes a network blip drop an email
    draft for good; ``tests/unit/m365/test_errors.py`` pins it for that reason.

    It is read off the **instance**, which is what lets a handler that has
    already mutated Graph veto its own retry: ``EmailOutbox._create`` clears it
    on the way out once the draft exists, because a release after that point
    would put a second draft in the mailbox.

    **Deliberately not a ``GraphApiError``.** Twelve per-item handlers across
    the extractors catch ``(GraphApiError, httpx.TransportError)`` so one
    unreadable chat or unfetchable attachment cannot kill a whole extractor.
    But this error escapes only after ``graph.max_retries`` attempts, which
    means the identity provider is unreachable -- and swallowing that per item
    would skip every remaining item and then record a *successful* sync with
    silently missing data. Sitting outside that hierarchy is what keeps those
    twelve handlers correct without editing any of them.
    """

    transient = True


class TokenCacheError(Exception):
    """Reading or writing the MSAL token cache file failed.

    **Deliberately not an ``OSError``**, which is what the code it replaces
    raised. The token cache is read on first use of ``DeviceCodeAuth`` and
    written after every acquisition, both inside the token provider -- and
    ``GraphClient._headers`` calls that provider from *within* the retry
    envelope, which catches transport errors and not filesystem ones. A bare
    ``OSError`` therefore travelled straight through the transport and into
    ``_attachment_helpers.py``'s ``except (..., OSError)`` arm, where a full
    disk read as an unreadable attachment: warned, skipped, next one tried,
    and the extractor recorded a **successful** sync missing every attachment.
    ``_file_helpers.py`` and ``commands/_catalog.py`` have the same arm.

    Nothing catches this. A broken token cache is not a per-item condition and
    not retryable -- it stops the unit and reaches ``cycle.py``'s recorder,
    which is the loud outcome the silent skip was hiding.
    """
