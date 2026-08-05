"""N named Entra apps, addressed by name, one token cache each.

Three apps run side by side in a typical deployment -- one for mail, one for
files, one for channel posting -- because pooling their scopes is what turns
"this outbox can only draft" from a permission into a promise. Before this
module each extra app arrived as its own loader module re-implementing env
expansion and cache-path resolution; both of those now happen once, in the
config loader, and this holds only the MSAL lifecycle.

Memoisation is the point of the class rather than an optimisation: one MSAL
`PublicClientApplication` per profile means one in-memory token cache per
profile, so a silent refresh on one app cannot serialise another app's cache
over it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from m365_brain.config import AuthProfileConfig
from m365_brain.m365.auth.device_code import DeviceCodeAuth


class AuthProfileError(Exception):
    """Raised when a profile is unknown, or cannot supply a CLI token provider."""


ProfileState = Literal["authenticated", "expired", "never_authenticated"]


@dataclass(frozen=True)
class ProfileStatus:
    """What a profile's cache can answer without prompting anybody."""

    name: str
    state: ProfileState
    accounts: tuple[str, ...]
    scopes: tuple[str, ...]
    token_cache_path: str


@runtime_checkable
class TokenProvider(Protocol):
    """A thing that can produce a bearer token.

    `GraphClient` takes a plain `Callable[[], str]`, and a bound method
    satisfies both -- so `provider()` returns the callable and this protocol
    exists for callers that want to name the shape.
    """

    def token(self) -> str: ...


class AuthProfiles:
    """Resolves profile names to MSAL apps, one app and one cache per name."""

    def __init__(self, profiles: dict[str, AuthProfileConfig]) -> None:
        self._profiles = dict(profiles)
        self._apps: dict[str, DeviceCodeAuth] = {}
        self._reject_shared_caches()

    def _reject_shared_caches(self) -> None:
        """Two profiles sharing a token cache defeats the reason to have two.

        MSAL keys its cache by client id, so a shared file does not literally
        mix tokens -- but it does put two apps' refresh tokens in one artifact,
        so revoking one means deleting the other's session too, and a
        `draft_only` app's isolation stops being auditable from the filesystem.
        Caught at construction because it is a static property of config.
        """
        seen: dict[str, str] = {}
        for name, profile in sorted(self._profiles.items()):
            owner = seen.get(profile.token_cache_path)
            if owner is not None:
                raise AuthProfileError(
                    f"auth.profiles.{name} and auth.profiles.{owner} share "
                    f"token_cache_path {profile.token_cache_path!r}; give each profile its own cache"
                )
            seen[profile.token_cache_path] = name

    def names(self) -> list[str]:
        """Every configured profile name, sorted."""
        return sorted(self._profiles)

    def config(self, name: str) -> AuthProfileConfig:
        """The profile's config block. Raises naming the available profiles."""
        try:
            return self._profiles[name]
        except KeyError:
            raise AuthProfileError(f"no auth profile named {name!r}; configured profiles: {self.names()}") from None

    def scopes(self, name: str) -> list[str]:
        """The scopes granted to one profile's app.

        Read by the outbox registry's tier guard: a `draft_only` outbox whose
        profile carries a send scope is a configuration contradiction, and the
        only place that pairing is visible is here.
        """
        return list(self.config(name).scopes)

    def provider(self, name: str) -> Callable[[], str]:
        """A memoised token callable for one profile.

        `client_secret is None` selects the device-code public-client flow,
        which is the CLI path. A confidential client has no bearer token
        outside a user's web session, so it raises rather than silently
        returning a callable that cannot work.
        """
        return self._app(name).get_token

    def login(self, name: str) -> None:
        """Force the interactive device-code flow for one profile."""
        self._app(name).login()

    def status(self, name: str) -> ProfileStatus:
        """Report the profile's cache state without prompting.

        `never_authenticated` and `expired` are deliberately distinct: the
        first is "run login", the second is "the refresh token is dead, run
        login *and* expect a consent prompt".
        """
        profile = self.config(name)
        state: ProfileState
        if not Path(profile.token_cache_path).exists():
            state = "never_authenticated"
            accounts: tuple[str, ...] = ()
        else:
            app = self._app(name)
            accounts = tuple(app.account_names())
            state = "authenticated" if app.cached_token() is not None else "expired"
        return ProfileStatus(
            name=name,
            state=state,
            accounts=accounts,
            scopes=tuple(profile.scopes),
            token_cache_path=profile.token_cache_path,
        )

    def _app(self, name: str) -> DeviceCodeAuth:
        profile = self.config(name)
        if profile.client_secret is not None:
            raise AuthProfileError(
                f"auth profile {name!r} sets client_secret, so it is a confidential client. "
                "Confidential clients hold no token outside a user's web session -- use "
                "m365_brain.m365.auth.token_provider.make_web_token_provider with a token store."
            )
        cached = self._apps.get(name)
        if cached is None:
            cached = DeviceCodeAuth(profile)
            self._apps[name] = cached
        return cached
