"""Smoke tests — lightweight health checks that work in any environment."""

import pytest

try:
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    _SKIP = False
except Exception:
    _SKIP = True


@pytest.mark.skipif(_SKIP, reason="Backend deps not available")
def test_health():
    r = client.get("/")
    assert r.status_code == 200
    assert "APEX" in r.json().get("message", "")


@pytest.mark.skipif(_SKIP, reason="Backend deps not available")
def test_kpi_summary():
    r = client.get("/api/kpi/summary")
    assert r.status_code == 200
