"""A rejected session is logged on the logger of the view that rejected it.

The shared gate takes the view's logger rather than its own, so an operator
filtering on ``django_spec_mcp.transport.async_streamable_http_view`` sees the
async endpoint's rejections and only those.
"""

from __future__ import annotations

import logging

import pytest

from tests.transport.conftest import Endpoint


async def test_a_session_rejection_logs_on_its_views_logger(
    endpoint: Endpoint, caplog: pytest.LogCaptureFixture
) -> None:
    view = "async_streamable_http_view" if endpoint.is_async else "streamable_http_view"
    with caplog.at_level(logging.WARNING):
        response = await endpoint.post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, session_id="unknown"
        )
    assert response.status_code == 404
    [record] = [r for r in caplog.records if r.getMessage().startswith("Session rejected")]
    assert record.name == f"django_spec_mcp.transport.{view}"
