"""Admin test fixtures — auto-marks all tests with 'admin' marker."""

import pytest


def pytest_collection_modifyitems(items):
    """Mark all test_admin tests with the 'admin' marker."""
    for item in items:
        if "test_admin" in str(item.fspath):
            item.add_marker(pytest.mark.admin)
