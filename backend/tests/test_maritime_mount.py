"""The maritime data router is mounted into the main app ahead of the /api/{path} catch-all."""
from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

TOKEN = "test-only-maritime_data-token-12345678901234567890"


def test_maritime_routes_are_mounted_when_token_is_set(monkeypatch):
    monkeypatch.setenv("MARITIME_DATA_API_TOKEN", TOKEN)
    monkeypatch.setenv("MARITIME_DATA_DATABASE_URL", "sqlite://")
    from pyapi import workspace_api

    module = importlib.reload(workspace_api)
    try:
        client = TestClient(module.app)
        assert client.get("/api/maritime-data/factors").status_code == 401
        response = client.get("/api/maritime-data/factors", headers={"Authorization": "Bearer " + TOKEN})
        assert response.status_code == 200
    finally:
        module.maritime_engine.dispose()
        monkeypatch.delenv("MARITIME_DATA_API_TOKEN")
        importlib.reload(module)


def test_maritime_routes_are_absent_without_token(monkeypatch):
    monkeypatch.delenv("MARITIME_DATA_API_TOKEN", raising=False)
    from pyapi import workspace_api

    module = importlib.reload(workspace_api)
    assert module.maritime_engine is None
    assert not any(getattr(route, "path", "").startswith("/api/maritime-data") for route in module.app.routes)
