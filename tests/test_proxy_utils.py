"""
Tests for the shared proxy utilities in ``_proxy_utils``.

Covers the client-leak-fix code path: when ``client.send()`` raises,
the ``httpx.AsyncClient`` must be closed and the exception re-raised.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cardea.proxies._proxy_utils import proxy


def _make_mock_request() -> MagicMock:
    """Return a minimal mock that satisfies the ``Request`` interface."""
    req = MagicMock()
    req.method = "GET"
    req.stream.return_value = iter([])
    return req


def _make_mock_client(send_side_effect: BaseException) -> MagicMock:
    """Return a mock ``httpx.AsyncClient`` whose ``send()`` raises."""
    mock_client = MagicMock()
    mock_client.build_request.return_value = MagicMock()
    mock_client.send = AsyncMock(side_effect=send_side_effect)
    mock_client.aclose = AsyncMock()
    return mock_client


@pytest.mark.asyncio
@patch("cardea.proxies._proxy_utils.httpx.AsyncClient")
async def test_client_closed_on_send_failure(mock_client_cls):
    """If client.send() raises, the client must be closed before re-raising."""
    mock_client = _make_mock_client(ConnectionError("simulated upstream failure"))
    mock_client_cls.return_value = mock_client

    with pytest.raises(ConnectionError):
        await proxy(_make_mock_request(), "https://upstream.example.com/path", {})

    mock_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
@patch("cardea.proxies._proxy_utils.httpx.AsyncClient")
async def test_client_closed_on_cancellation(mock_client_cls):
    """CancelledError (a BaseException) must also trigger client cleanup."""
    mock_client = _make_mock_client(asyncio.CancelledError())
    mock_client_cls.return_value = mock_client

    with pytest.raises(asyncio.CancelledError):
        await proxy(_make_mock_request(), "https://upstream.example.com/path", {})

    mock_client.aclose.assert_awaited_once()
