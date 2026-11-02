"""A body of exactly ``max_request_bytes`` is admitted; one byte more is a ``413``.

The limit is a size the body may reach, so the comparison is strict. Run on
both views, which each read their body through ``read_message``.
"""

from __future__ import annotations

from django_spec_mcp.config.build_mcp_config import build_mcp_config
from tests.testapp.mcp import build_server
from tests.transport.conftest import Endpoint, initialize_body


def _padded(size: int) -> str:
    """A valid ``initialize`` request, trailing-space padded to exactly ``size`` bytes."""
    body: str = initialize_body()
    assert len(body) <= size
    return body.ljust(size)


async def test_a_body_of_exactly_the_limit_is_admitted(endpoint: Endpoint) -> None:
    limit: int = len(initialize_body())
    server = build_server(config=build_mcp_config(max_request_bytes=limit))
    with endpoint.mount(server):
        response = await endpoint.post(_padded(limit))
    assert response.status_code == 200, response.content
    assert response["Mcp-Session-Id"]


async def test_a_body_one_byte_over_the_limit_is_a_413(endpoint: Endpoint) -> None:
    limit: int = len(initialize_body())
    server = build_server(config=build_mcp_config(max_request_bytes=limit))
    with endpoint.mount(server):
        response = await endpoint.post(_padded(limit + 1))
    assert response.status_code == 413, response.content
    assert response.json()["error"]["message"] == "Request body too large"
