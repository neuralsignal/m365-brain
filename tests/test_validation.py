"""Tests for input validation."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from m365_extract.config.errors import ConfigError
from m365_extract.validation import validate_user_id


class TestValidateUserId:
    def test_accepts_valid_lowercase_uuid(self) -> None:
        validate_user_id("550e8400-e29b-41d4-a716-446655440000")

    def test_accepts_valid_uppercase_uuid(self) -> None:
        validate_user_id("550E8400-E29B-41D4-A716-446655440000")

    def test_accepts_valid_mixed_case_uuid(self) -> None:
        validate_user_id("550e8400-E29B-41d4-A716-446655440000")

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ConfigError, match="Invalid user_id format"):
            validate_user_id("")

    def test_rejects_path_traversal(self) -> None:
        with pytest.raises(ConfigError, match="Invalid user_id format"):
            validate_user_id("../../../etc/passwd")

    def test_rejects_relative_path(self) -> None:
        with pytest.raises(ConfigError, match="Invalid user_id format"):
            validate_user_id("../admin")

    def test_rejects_non_uuid_string(self) -> None:
        with pytest.raises(ConfigError, match="Invalid user_id format"):
            validate_user_id("not-a-uuid")

    def test_rejects_uuid_without_hyphens(self) -> None:
        with pytest.raises(ConfigError, match="Invalid user_id format"):
            validate_user_id("550e8400e29b41d4a716446655440000")

    def test_rejects_uuid_with_braces(self) -> None:
        with pytest.raises(ConfigError, match="Invalid user_id format"):
            validate_user_id("{550e8400-e29b-41d4-a716-446655440000}")

    def test_rejects_uuid_with_trailing_slash(self) -> None:
        with pytest.raises(ConfigError, match="Invalid user_id format"):
            validate_user_id("550e8400-e29b-41d4-a716-446655440000/")

    def test_error_message_includes_value(self) -> None:
        with pytest.raises(ConfigError, match="'badval'"):
            validate_user_id("badval")

    @given(st.uuids())
    def test_accepts_any_hypothesis_uuid(self, uuid_val) -> None:
        validate_user_id(str(uuid_val))

    @given(st.from_regex(r".*(\.\.|/).*", fullmatch=True))
    def test_rejects_paths(self, path_like) -> None:
        with pytest.raises(ConfigError):
            validate_user_id(path_like)
