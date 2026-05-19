"""Tests for html_to_markdown converter."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from m365_extract.converters.html_to_md import html_to_markdown


@pytest.mark.parametrize(
    "html",
    [
        "",
        "   \n\t  ",
        "   ",
        "\n",
        "\t",
    ],
)
def test_falsy_or_whitespace_returns_empty(html: str) -> None:
    assert html_to_markdown(html, strip_images=True) == ""


def test_simple_html_converted() -> None:
    result = html_to_markdown("<p>hello</p>", strip_images=True)
    assert "hello" in result


def test_heading_uses_atx_style() -> None:
    result = html_to_markdown("<h1>Title</h1>", strip_images=True)
    assert result.startswith("# ")


def test_strip_images_true_drops_img_tag() -> None:
    result = html_to_markdown('<p>before<img src="x.png" alt="alt">after</p>', strip_images=True)
    assert "x.png" not in result
    assert "alt" not in result


def test_strip_images_false_preserves_img_as_markdown() -> None:
    result = html_to_markdown('<p>see <img src="local/path.png" alt="diagram"></p>', strip_images=False)
    assert "local/path.png" in result


@given(st.text())
def test_arbitrary_input_no_exception(html: str) -> None:
    result = html_to_markdown(html, strip_images=True)
    assert isinstance(result, str)
