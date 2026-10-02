"""Tests for Teams attachment download resolution helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from hypothesis import given
from hypothesis import strategies as st

from m365_brain.m365.extractors._teams_attachment_download import (
    encode_share_url,
    resolve_reference_bytes,
)


class TestEncodeShareUrl:
    def test_prefix(self) -> None:
        assert encode_share_url("https://example.com/file").startswith("u!")

    def test_no_padding(self) -> None:
        assert "=" not in encode_share_url("https://example.com/file")

    @given(st.text(min_size=1))
    def test_always_starts_with_u_bang(self, url: str) -> None:
        assert encode_share_url(url).startswith("u!")

    @given(st.text(min_size=1))
    def test_never_contains_padding(self, url: str) -> None:
        assert "=" not in encode_share_url(url)


class TestResolveReferenceBytes:
    def test_returns_bytes_on_success(self) -> None:
        client = MagicMock()
        client.get.return_value = {
            "size": 100,
            "@microsoft.graph.downloadUrl": "https://dl.example.com/file",
        }
        client.get_bytes.return_value = b"file-content"

        result = resolve_reference_bytes(client, "https://share.example.com/f", max_bytes=1024)

        assert result == b"file-content"
        client.get_bytes.assert_called_once_with("https://dl.example.com/file")

    def test_returns_none_when_too_large(self) -> None:
        client = MagicMock()
        client.get.return_value = {"size": 2000}

        result = resolve_reference_bytes(client, "https://share.example.com/f", max_bytes=1024)

        assert result is None

    def test_returns_none_when_no_download_url(self) -> None:
        client = MagicMock()
        client.get.return_value = {"size": 100}

        result = resolve_reference_bytes(client, "https://share.example.com/f", max_bytes=1024)

        assert result is None
