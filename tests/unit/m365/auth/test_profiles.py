"""Named Entra profiles: one MSAL app and one cache per name.

The property under test is isolation. A profile registry that quietly handed
two names the same MSAL app would still pass every happy-path assertion --
tokens would be returned, logins would work -- and would silently pool the
scope grants that `draft_only` depends on. So the tests assert on identity of
the underlying app and on the cache path each one writes.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from m365_brain.config import AuthProfileConfig
from m365_brain.m365.auth.profiles import AuthProfileError, AuthProfiles

# MSAL resolves its authority over the network at construction time, so every
# test that reaches an MSAL app patches the class. A fresh MagicMock per call
# is what keeps the identity assertions below meaningful.
MSAL_APP = "m365_brain.m365.auth.device_code.msal.PublicClientApplication"


def _profile(client_id: str, cache: str, scopes: list[str], secret: str | None) -> AuthProfileConfig:
    return AuthProfileConfig(
        client_id=client_id,
        tenant_id="tenant-1",
        scopes=scopes,
        token_cache_path=cache,
        client_secret=secret,
    )


@pytest.fixture()
def two_profiles(tmp_path):
    return {
        "mail": _profile("mail-app", str(tmp_path / "mail.json"), ["Mail.ReadWrite", "offline_access"], None),
        "files": _profile("files-app", str(tmp_path / "files.json"), ["Files.ReadWrite.All"], None),
    }


class TestResolution:
    def test_unknown_profile_names_itself_and_the_alternatives(self, two_profiles):
        profiles = AuthProfiles(two_profiles)

        with pytest.raises(AuthProfileError) as excinfo:
            profiles.provider("teams")

        message = str(excinfo.value)
        assert "'teams'" in message
        assert "['files', 'mail']" in message

    def test_names_and_scopes_come_from_config(self, two_profiles):
        profiles = AuthProfiles(two_profiles)

        assert profiles.names() == ["files", "mail"]
        assert profiles.scopes("mail") == ["Mail.ReadWrite", "offline_access"]

    def test_scopes_returns_a_copy_so_a_caller_cannot_widen_the_grant(self, two_profiles):
        profiles = AuthProfiles(two_profiles)

        profiles.scopes("mail").append("Mail.Send")

        assert "Mail.Send" not in profiles.scopes("mail")


class TestIsolation:
    @patch(MSAL_APP, side_effect=lambda *a, **k: MagicMock())
    def test_each_profile_gets_its_own_msal_app_and_cache(self, msal_app, two_profiles):
        profiles = AuthProfiles(two_profiles)

        mail = profiles._app("mail")
        files = profiles._app("files")

        assert mail is not files
        assert mail._config.token_cache_path != files._config.token_cache_path
        assert mail._cache is not files._cache
        assert mail._app is not files._app
        client_ids = {call.args[0] for call in msal_app.call_args_list}
        assert client_ids == {"mail-app", "files-app"}

    @patch(MSAL_APP, side_effect=lambda *a, **k: MagicMock())
    def test_the_app_is_memoised_per_profile(self, msal_app, two_profiles):
        profiles = AuthProfiles(two_profiles)

        assert profiles._app("mail") is profiles._app("mail")
        assert msal_app.call_count == 1

    @patch(MSAL_APP, side_effect=lambda *a, **k: MagicMock())
    def test_the_provider_is_bound_to_that_profiles_app(self, msal_app, two_profiles):
        profiles = AuthProfiles(two_profiles)

        provider = profiles.provider("mail")

        assert provider.__self__ is profiles._app("mail")

    def test_two_profiles_sharing_a_cache_path_is_rejected_at_construction(self, tmp_path):
        shared = str(tmp_path / "one.json")
        profiles = {
            "mail": _profile("mail-app", shared, ["Mail.ReadWrite"], None),
            "files": _profile("files-app", shared, ["Files.ReadWrite.All"], None),
        }

        with pytest.raises(AuthProfileError) as excinfo:
            AuthProfiles(profiles)

        assert "share" in str(excinfo.value)
        assert shared in str(excinfo.value)


class TestConfidentialClients:
    def test_a_client_secret_profile_refuses_to_hand_out_a_cli_provider(self, tmp_path):
        profiles = AuthProfiles({"web": _profile("web-app", str(tmp_path / "web.json"), ["Mail.Read"], "s3cret")})

        with pytest.raises(AuthProfileError) as excinfo:
            profiles.provider("web")

        assert "make_web_token_provider" in str(excinfo.value)


class TestStatus:
    def test_no_cache_file_reports_never_authenticated_without_touching_msal(self, two_profiles):
        profiles = AuthProfiles(two_profiles)

        status = profiles.status("mail")

        assert status.state == "never_authenticated"
        assert status.accounts == ()
        assert status.scopes == ("Mail.ReadWrite", "offline_access")
        assert profiles._apps == {}, "a status check must not construct an MSAL app it does not need"

    @patch(MSAL_APP)
    def test_a_cache_that_cannot_refresh_reports_expired(self, msal_app, two_profiles, tmp_path):
        (tmp_path / "mail.json").write_text("{}", encoding="utf-8")
        msal_app.return_value.get_accounts.return_value = [{"username": "a@example.com"}]
        msal_app.return_value.acquire_token_silent.return_value = None

        status = AuthProfiles(two_profiles).status("mail")

        assert status.state == "expired"
        assert status.accounts == ("a@example.com",)

    @patch(MSAL_APP)
    def test_a_usable_cache_reports_authenticated(self, msal_app, two_profiles, tmp_path):
        (tmp_path / "mail.json").write_text("{}", encoding="utf-8")
        msal_app.return_value.get_accounts.return_value = [{"username": "a@example.com"}]
        msal_app.return_value.acquire_token_silent.return_value = {"access_token": "tok"}

        assert AuthProfiles(two_profiles).status("mail").state == "authenticated"
