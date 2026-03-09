"""
Tests for the disabled_endpoints configuration feature.

Temporarily writes a config.toml with [gmail] disabled_endpoints = ["send"]
to verify that specific endpoints return 403 while the rest of the module
continues to work.
"""

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"

DISABLED_CONFIG = """\
[modules]
gmail = true

[gmail]
disabled_endpoints = ["send"]
"""


@pytest.fixture()
def disabled_send_app():
    """Create a fresh FastAPI app with Gmail send endpoint disabled."""
    # Save whatever config exists (conftest may have created one).
    original = CONFIG_PATH.read_text() if CONFIG_PATH.exists() else None

    CONFIG_PATH.write_text(DISABLED_CONFIG)

    import cardea.app

    importlib.reload(cardea.app)
    yield TestClient(cardea.app.app)

    # Restore original config and reload to leave state clean.
    if original is not None:
        CONFIG_PATH.write_text(original)
    elif CONFIG_PATH.exists():
        CONFIG_PATH.unlink()
    importlib.reload(cardea.app)


def test_disabled_send_returns_403(disabled_send_app):
    """POST /gmail/send returns 403 when disabled by config."""
    response = disabled_send_app.post(
        "/gmail/send",
        json={"to": "x@example.com", "subject": "test", "body": "body"},
    )
    assert response.status_code == 403
    assert "disabled" in response.json()["detail"].lower()


def test_non_disabled_endpoint_still_works(disabled_send_app):
    """GET /gmail/messages is NOT disabled and should NOT return 403.

    It will return 503 (missing credentials) which proves the route is
    still active — 403 would mean it was incorrectly blocked.
    """
    response = disabled_send_app.get("/gmail/messages")
    # 503 = credential check, meaning the endpoint is reachable (not blocked)
    assert response.status_code == 503


def test_health_still_works(disabled_send_app):
    """The /health meta-endpoint is never affected by disabled_endpoints."""
    response = disabled_send_app.get("/health")
    assert response.status_code == 200
