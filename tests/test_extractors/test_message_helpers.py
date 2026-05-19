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
        assert extract_content(msg, {}) == "hello"

    def test_html_content(self) -> None:
        msg = {"body": {"contentType": "html", "content": "<b>Hello</b>"}}
        result = extract_content(msg, {})
        assert "Hello" in result

    def test_html_empty_content(self) -> None:
        msg = {"body": {"contentType": "html", "content": ""}}
        assert extract_content(msg, {}) == ""

    def test_missing_body(self) -> None:
        assert extract_content({}, {}) == ""

    def test_hosted_image_src_rewritten(self) -> None:
        html = (
            '<p>see this: <img src="https://graph.microsoft.com/v1.0/chats/19:abc/'
            'messages/12345/hostedContents/eyJ0eXBlIjoxLCJpZCI6IjI4OTAifQ==/$value" '
            'width="200"></p>'
        )
        msg = {"body": {"contentType": "html", "content": html}}
        hosted_map = {"eyJ0eXBlIjoxLCJpZCI6IjI4OTAifQ==": "attachments/msg-1/inline_0.png"}
        result = extract_content(msg, hosted_map)
        assert "attachments/msg-1/inline_0.png" in result
        assert "hostedContents" not in result

    @given(content=st.text(alphabet=st.characters(blacklist_categories=("Cc", "Cs")), min_size=1))
    def test_html_content_returns_string(self, content: str) -> None:
        msg = {"body": {"contentType": "html", "content": content}}
        result = extract_content(msg, {})
        assert isinstance(result, str)


class TestRewriteHostedImageSrcs:
    def test_empty_map_returns_html_unchanged(self) -> None:
        from m365_extract.extractors._message_helpers import rewrite_hosted_image_srcs

        html = '<img src="https://example.com/hostedContents/xyz/$value">'
        assert rewrite_hosted_image_srcs(html, {}) == html

    def test_unknown_hid_is_left_alone(self) -> None:
        from m365_extract.extractors._message_helpers import rewrite_hosted_image_srcs

        html = '<img src="https://x/hostedContents/UNKNOWN/$value">'
        result = rewrite_hosted_image_srcs(html, {"OTHER": "attachments/m/0.png"})
        assert "UNKNOWN" in result
        assert "attachments/m/0.png" not in result

    def test_single_quote_attribute_rewritten(self) -> None:
        from m365_extract.extractors._message_helpers import rewrite_hosted_image_srcs

        html = "<img src='https://x/hostedContents/HID1/$value'>"
        result = rewrite_hosted_image_srcs(html, {"HID1": "attachments/m/0.png"})
        assert "attachments/m/0.png" in result

    def test_multiple_images_rewritten_independently(self) -> None:
        from m365_extract.extractors._message_helpers import rewrite_hosted_image_srcs

        html = '<img src="https://x/hostedContents/A/$value"><img src="https://x/hostedContents/B/$value">'
        result = rewrite_hosted_image_srcs(html, {"A": "a.png", "B": "b.png"})
        assert 'src="a.png"' in result
        assert 'src="b.png"' in result

    def test_non_hosted_src_is_untouched(self) -> None:
        from m365_extract.extractors._message_helpers import rewrite_hosted_image_srcs

        html = '<img src="https://cdn.example.com/logo.png">'
        result = rewrite_hosted_image_srcs(html, {"X": "attachments/m/0.png"})
        assert result == html

    @given(hid=st.text(alphabet=st.characters(whitelist_categories=("L", "N")), min_size=1, max_size=20))
    def test_replacement_property_known_hid_always_replaced(self, hid: str) -> None:
        from m365_extract.extractors._message_helpers import rewrite_hosted_image_srcs

        html = f'<img src="https://x/hostedContents/{hid}/$value">'
        result = rewrite_hosted_image_srcs(html, {hid: "attachments/m/0.png"})
        assert "attachments/m/0.png" in result
        assert f"hostedContents/{hid}/$value" not in result
