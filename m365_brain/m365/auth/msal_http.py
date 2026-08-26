"""The one place in this package that knows MSAL talks over ``requests``.

Everything aimed at Graph goes through ``GraphClient`` and therefore ``httpx``.
Everything aimed at ``login.microsoftonline.com`` goes through MSAL, which
carries its own ``requests`` session. That second transport arrives with two
properties nobody chose, both of which bit in production, and both of which are
contained here rather than repeated at each of the six MSAL call sites.

**No timeout.** MSAL never passes one and ``requests`` without a ``timeout``
waits forever, so a black-holed connection to the identity provider hangs the
sync daemon with nothing above it to notice. ``TimeoutSession`` supplies the
value ``graph.timeout_seconds`` already sets for every Graph call: one config
key answering "how long may an M365 HTTP call take" for both transports, rather
than two keys guaranteed to drift.

**A foreign exception type.** ``requests.exceptions.RequestException`` means
nothing to the retry loop in ``m365/client.py``, which speaks ``httpx``.
``auth_transport_errors`` translates it here into ``AuthTransportError``.
The two shortcuts both fail: catching ``RequestException`` in the transport
core drags a provider type inward, and catching ``OSError`` -- which
``RequestException`` happens to subclass -- would swallow every filesystem
error in the same breath.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import requests

from m365_brain.m365.errors import AuthTransportError


class TimeoutSession(requests.Session):
    """A ``requests`` session that refuses to wait forever.

    Passing ``http_client`` is the supported way in: MSAL then skips building
    its own session and calls this one through its throttling decorator, whose
    ``get``/``post`` delegate here -- so overriding ``request`` alone covers
    every call. ``setdefault`` rather than assignment, so a caller passing an
    explicit timeout still wins. The one thing given up is the
    ``HTTPAdapter(max_retries=1)`` MSAL mounts on a session it builds itself;
    ``GraphClient``'s envelope now retries these calls with real backoff, which
    is the larger of the two.
    """

    def __init__(self, timeout_seconds: int) -> None:
        super().__init__()
        self._timeout_seconds = timeout_seconds

    def request(self, *args: Any, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self._timeout_seconds)
        return super().request(*args, **kwargs)


@contextmanager
def auth_transport_errors() -> Iterator[None]:
    """Translate a ``requests`` transport fault into ``AuthTransportError``.

    Wraps MSAL calls and nothing else. Everything MSAL reports *in band* --
    the error dicts for bad credentials, a revoked consent, a dead refresh
    token -- passes straight through untouched, because none of it is
    transient and retrying it ``graph.max_retries`` times only delays the
    truth by the length of the backoff.

    The message keeps the original type name: by the time this reaches
    ``graph.transport_error`` in the log, "ConnectionError" and "ReadTimeout"
    are the whole diagnosis.
    """
    try:
        yield
    except requests.exceptions.RequestException as exc:
        raise AuthTransportError(f"{type(exc).__name__}: {exc}") from exc
