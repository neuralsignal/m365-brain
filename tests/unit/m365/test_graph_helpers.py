"""Tests for the Graph client helper functions.

Three of these carry security weight and are tested hardest:
``_is_allowed_download_domain`` is the SSRF guard on binary downloads,
``_sanitize_log_url`` is what keeps SAS tokens out of the logs, and
``validated_download_ref`` is the entry point that combines the two.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from m365_brain.m365.client import GraphApiError
from m365_brain.m365.graph_helpers import (
    ALLOWED_DOWNLOAD_DOMAINS,
    _extract_graph_error,
    _friendly_error,
    _is_allowed_download_domain,
    _sanitize_log_url,
    validated_download_ref,
)

BLOCKED_URLS = [
    # The classic suffix-confusion attack: an allowed domain as a *prefix* of the real host.
    "https://graph.microsoft.com.evil.test/steal",
    "https://tenant.sharepoint.com.attacker.example/payload",
    # Suffix glued on without the dot separator.
    "https://evilsharepoint.com/payload",
    "https://notsvc.ms/payload",
    # Userinfo trick — the real host is after the '@'.
    "https://tenant.sharepoint.com@evil.test/steal",
    # Allowed domain relegated to path or fragment.
    "https://evil.test/https://tenant.sharepoint.com/file",
    "https://evil.test/x#tenant.sharepoint.com",
    # Unrelated hosts, including Azure storage and the cloud metadata endpoint.
    "https://attacker.blob.core.windows.net/container/blob",
    "https://169.254.169.254/latest/meta-data/",
]


class TestFriendlyError:
    @pytest.mark.parametrize(
        "code,hint_fragment",
        [
            ("Authorization_RequestDenied", "API permissions"),
            ("InvalidAuthenticationToken", "m365-brain --config config.yaml auth login"),
        ],
    )
    def test_known_codes_get_an_actionable_hint(self, code: str, hint_fragment: str) -> None:
        first, second = _friendly_error(403, code, "denied", "/me/messages").split("\n")
        assert first == f"Graph API error on /me/messages: HTTP 403 — {code}: denied"
        assert second.startswith("  Hint: ")
        assert hint_fragment in second

    def test_unknown_code_is_a_single_line_without_a_hint(self) -> None:
        message = _friendly_error(400, "SomeBrandNewError", "Something unexpected.", "/me")
        assert message == "Graph API error on /me: HTTP 400 — SomeBrandNewError: Something unexpected."


class TestIsAllowedDownloadDomain:
    @pytest.mark.parametrize(
        "url",
        [
            "https://tenant.sharepoint.com/sites/docs/file.docx",
            "https://tenant-my.sharepoint.com/personal/user/file.docx",
            "https://public.1drv.com/y4m/file",
            "https://graph.microsoft.com/v1.0/me/photo/$value",
            "https://files.cdn.office.net/assets/doc.docx",
            "https://svc.ms/download/1",
            "https://TENANT.SharePoint.Com/sites/docs/file.docx",
        ],
    )
    def test_microsoft_hosts_allowed(self, url: str) -> None:
        assert _is_allowed_download_domain(url) is True

    @pytest.mark.parametrize("url", [*BLOCKED_URLS, "file:///etc/passwd", "tenant.sharepoint.com/no-scheme", ""])
    def test_lookalike_and_foreign_hosts_rejected(self, url: str) -> None:
        assert _is_allowed_download_domain(url) is False

    @given(
        suffix=st.sampled_from(sorted(ALLOWED_DOWNLOAD_DOMAINS)),
        attacker=st.from_regex(r"[a-z]{3,10}\.(test|example|invalid)", fullmatch=True),
    )
    @settings(max_examples=50)
    def test_allowed_suffix_never_wins_when_it_is_not_the_suffix(self, suffix: str, attacker: str) -> None:
        """An allowed domain sitting *inside* the host must never grant access."""
        assert _is_allowed_download_domain(f"https://host{suffix}.{attacker}/payload") is False


class TestSanitizeLogUrl:
    @pytest.mark.parametrize(
        "url,expected",
        [
            (
                "https://tenant.sharepoint.com/personal/u/f.docx?sv=2021-06-08&sig=SUPERSECRET&se=2030",
                "https://tenant.sharepoint.com/personal/u/f.docx",
            ),
            ("https://host.test/path?q=1#tok=2", "https://host.test/path"),
            ("https://host.test/path#tok=2", "https://host.test/path"),
            ("https://tenant.sharepoint.com/sites/x/report.pdf", "https://tenant.sharepoint.com/sites/x/report.pdf"),
        ],
    )
    def test_keeps_scheme_host_and_path_only(self, url: str, expected: str) -> None:
        assert _sanitize_log_url(url) == expected

    @given(
        host=st.from_regex(r"[a-z]{3,10}\.sharepoint\.com", fullmatch=True),
        path=st.from_regex(r"(/[a-z0-9]{1,10}){1,4}", fullmatch=True),
        secret=st.from_regex(r"[A-Za-z0-9]{16,64}", fullmatch=True),
    )
    @settings(max_examples=50)
    def test_query_secrets_never_survive(self, host: str, path: str, secret: str) -> None:
        sanitized = _sanitize_log_url(f"https://{host}{path}?sv=2021-06-08&sig={secret}")
        assert secret not in sanitized
        assert "?" not in sanitized
        assert sanitized == f"https://{host}{path}"


class TestValidatedDownloadRef:
    @pytest.mark.parametrize(
        "url,expected",
        [
            # Allowed absolute CDN URL — the SAS token is dropped from the log ref.
            ("https://tenant.sharepoint.com/dl/f.docx?sig=SECRET", "https://tenant.sharepoint.com/dl/f.docx"),
            # Graph-relative paths are ours, not attacker-supplied, and pass through verbatim.
            ("/me/drive/items/abc/content", "/me/drive/items/abc/content"),
        ],
    )
    def test_permitted_urls_yield_a_token_free_log_ref(self, url: str, expected: str) -> None:
        assert validated_download_ref(url) == expected

    @pytest.mark.parametrize("url", BLOCKED_URLS)
    def test_untrusted_hosts_are_refused(self, url: str) -> None:
        with pytest.raises(GraphApiError, match="not an allowed Microsoft domain") as exc_info:
            validated_download_ref(url)
        assert exc_info.value.status_code is None

    def test_the_refusal_message_does_not_echo_the_query_string(self) -> None:
        """Blocking must not itself leak the credential that rode along in the URL."""
        with pytest.raises(GraphApiError) as exc_info:
            validated_download_ref("https://evil.test/steal?sig=SUPERSECRET&sv=2021")
        assert "SUPERSECRET" not in str(exc_info.value)
        assert "?" not in str(exc_info.value)


class TestExtractGraphError:
    def test_truncates_message_to_the_configured_length(self) -> None:
        body = json.dumps({"error": {"code": "BadRequest", "message": "x" * 900}})
        code, message = _extract_graph_error(body, 200)
        assert code == "BadRequest"
        assert message == "x" * 200

    @pytest.mark.parametrize(
        "body",
        [
            "<html>500 for user@company.test</html>",
            json.dumps({"status": "failed", "detail": "pii@example.test"}),
            json.dumps({"error": {"code": "NoMessageKey"}}),
            json.dumps({"error": "a string, not an object"}),
            "",
        ],
    )
    def test_unparseable_body_never_leaks_its_content(self, body: str) -> None:
        assert _extract_graph_error(body, 200) == ("unknown", "non-json response")
