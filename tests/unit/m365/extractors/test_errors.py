"""Tests for the extractor error hierarchy.

The hierarchy is the contract: callers catch ``ExtractorError`` to mean "an
extractor failed for a non-Graph reason", so ``MessageStoreError`` must stay
underneath it and outside ``GraphApiError``.
"""

from __future__ import annotations

import pytest

from m365_brain.m365.client import GraphApiError
from m365_brain.m365.extractors.errors import ExtractorError, MessageStoreError


class TestErrorHierarchy:
    def test_message_store_error_is_caught_as_extractor_error(self) -> None:
        with pytest.raises(ExtractorError) as exc_info:
            raise MessageStoreError("corrupt line 3")
        assert isinstance(exc_info.value, MessageStoreError)
        assert str(exc_info.value) == "corrupt line 3"

    def test_extractor_errors_are_not_graph_errors(self) -> None:
        """A store failure must not be mistaken for a transport failure by the retry paths."""
        assert not issubclass(ExtractorError, GraphApiError)
        assert not issubclass(MessageStoreError, GraphApiError)

    def test_plain_extractor_error_is_not_a_message_store_error(self) -> None:
        """The leaf type must stay narrower than its base, so callers can discriminate."""
        assert not isinstance(ExtractorError("generic"), MessageStoreError)
        assert issubclass(MessageStoreError, ExtractorError)
