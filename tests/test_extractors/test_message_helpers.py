"""Tests for shared message extraction helpers."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from m365_extract.extractors._message_helpers import extract_content, extract_sender


class TestExtractSender:
    def test_user_sender_with_display_name(self) -> None:
        msg = {"from": {"user": {"displayName": "Alice"}}}
        assert extract_sender(msg) == "Alice"

    def test_application_sender_with_display_name(self) -> None:
        msg = {"from": {"application": {"displayName": "MyBot"}}}
        assert extract_sender(msg) == "MyBot"

    def test_application_sender_without_display_name(self) -> None:
        msg = {"from": {"application": {"id": "some-id"}}}
        assert extract_sender(msg) == "Bot"

    def test_no_from_field(self) -> None:
        assert extract_sender({}) == ""

    def test_from_field_empty(self) -> None:
        msg = {"from": {}}
        assert extract_sender(msg) == ""

    def test_from_field_with_no_user_or_app(self) -> None:
        msg = {"from": {"emailAddress": {"name": "someone"}}}
        assert extract_sender(msg) == ""

    @given(name=st.text(min_size=1))
    def test_application_display_name_property(self, name: str) -> None:
        msg = {"from": {"application": {"displayName": name}}}
        assert extract_sender(msg) == name


class TestExtractContent:
    def test_plain_text(self) -> None:
        msg = {"body": {"contentType": "text", "content": "hello"}}
        assert extract_content(msg) == "hello"

    def test_html_content(self) -> None:
        msg = {"body": {"contentType": "html", "content": "<b>Hello</b>"}}
        result = extract_content(msg)
        assert "Hello" in result

    def test_html_empty_content(self) -> None:
        msg = {"body": {"contentType": "html", "content": ""}}
        assert extract_content(msg) == ""

    def test_missing_body(self) -> None:
        assert extract_content({}) == ""

    @given(content=st.text(alphabet=st.characters(blacklist_categories=("Cc", "Cs")), min_size=1))
    def test_html_content_returns_string(self, content: str) -> None:
        msg = {"body": {"contentType": "html", "content": content}}
        result = extract_content(msg)
        assert isinstance(result, str)
