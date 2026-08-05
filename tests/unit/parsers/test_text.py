"""Ported from the source package's slugify suite."""

from __future__ import annotations

from m365_brain.parsers.text import content_hash, file_checksum, slugify


def test_accents():
    assert slugify("Über Budget") == "uber-budget"


def test_empty_string():
    assert slugify("") == ""


def test_spaces():
    assert slugify("hello world") == "hello-world"


def test_special_chars():
    assert slugify("hello@world!") == "helloworld"


def test_leading_trailing_separators():
    # Hyphens survive (they match the \- escape); whitespace and underscores
    # collapse to a single hyphen.
    assert slugify("  hello  ") == "hello"
    assert slugify("hello_world") == "hello-world"


def test_unicode_all_stripped():
    assert slugify("日本語") == ""


def test_file_checksum_reads_bytes(tmp_path):
    path = tmp_path / "note.md"
    path.write_bytes(b"body")
    assert file_checksum(path) == content_hash("body")


def test_content_hash_differs_on_different_text():
    assert content_hash("a") != content_hash("b")
