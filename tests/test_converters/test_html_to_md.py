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
    assert html_to_markdown(html) == ""


def test_simple_html_converted() -> None:
    result = html_to_markdown("<p>hello</p>")
    assert "hello" in result


def test_heading_uses_atx_style() -> None:
    result = html_to_markdown("<h1>Title</h1>")
    assert result.startswith("# ")


@given(st.text())
def test_arbitrary_input_no_exception(html: str) -> None:
    result = html_to_markdown(html)
    assert isinstance(result, str)
