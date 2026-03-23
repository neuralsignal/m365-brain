"""Tests for the FastAPI app factory."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi import FastAPI

from m365_extract.web.app import create_app
from m365_extract.web.exceptions import WebConfigError


class TestCreateApp:
    def test_create_app_returns_fastapi(self, full_web_config):
        app = create_app(full_web_config)
        assert isinstance(app, FastAPI)

    def test_raises_without_web_config(self, full_web_config):
        config = replace(full_web_config, web=None)
        with pytest.raises(WebConfigError, match="WebConfig is required"):
            create_app(config)

    def test_health_integration(self, full_web_config):
        """Smoke test: health endpoint works through the full app stack."""
        from fastapi.testclient import TestClient

        app = create_app(full_web_config)

        with TestClient(app) as client:
            response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
