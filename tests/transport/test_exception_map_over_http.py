from __future__ import annotations

import json
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied
from django.test import AsyncClient, override_settings
from django_service_specs import PermissionCheck, ServiceSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.output import build_error_tool_result
from django_spec_mcp.protocol.types.tool_result import ToolResult
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.urlconf_for import urlconf_for
from tests.transport.conftest import Endpoint, initialize_body
from tests.utils import tool_error

pytestmark = pytest.mark.django_db(transaction=True)

LEGACY = "2025-11-25"
MODERN = "2026-07-28"
TOOL = "orders.place"


class TenantGone(Exception):
    pass


def _gone(exc: Exception) -> ToolResult:
    return build_error_tool_result(str(exc), error_type="tenant_gone")


def _server(spec: ServiceSpec, **kwargs: Any) -> MCPServer:
    server = MCPServer(
        name="exception-map",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        **kwargs,
    )
    server.register_tool(spec, name=TOOL, description="d")
    return server


def _raises() -> None:
    raise TenantGone("from the service")


def _service_server(**kwargs: Any) -> MCPServer:
    return _server(ServiceSpec(service=_raises, permissions=[Unrestricted()]), **kwargs)


class _RaisingCheck(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:
        raise TenantGone("from the check")


def _check_server(**kwargs: Any) -> MCPServer:
    return _server(ServiceSpec(service=lambda: None, permissions=[_RaisingCheck()]), **kwargs)


def _request(era: str, session_id: str | None, *, progress: bool = False) -> dict[str, Any]:
    """The keyword arguments of a POST calling ``TOOL`` in ``era``."""
    meta: dict[str, Any] = {"progressToken": "p1"} if progress else {}
    headers: dict[str, str] = {"Mcp-Protocol-Version": era}
    if era == MODERN:
        meta = {
            **meta,
            "io.modelcontextprotocol/protocolVersion": MODERN,
            "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0.0"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        headers["Mcp-Method"] = "tools/call"
        headers["Mcp-Name"] = TOOL
    else:
        assert session_id is not None
        headers["Mcp-Session-Id"] = session_id
    params = {"name": TOOL, "arguments": {}, "_meta": meta}
    return {
        "data": json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": params}),
        "content_type": "application/json",
        "headers": headers,
    }


async def _called(endpoint: Endpoint, server: MCPServer, era: str) -> Any:
    with endpoint.mount(server):
        session_id = await endpoint.initialize() if era == LEGACY else None
        return await endpoint.send("post", **_request(era, session_id))


@pytest.mark.parametrize("era", [LEGACY, MODERN])
@pytest.mark.parametrize(
    "server_for", [_service_server, _check_server], ids=["from-the-service", "from-a-check"]
)
async def test_each_view_answers_a_mapped_exception_as_its_handler_says(
    endpoint: Endpoint, era: str, server_for: Any
) -> None:
    response = await _called(endpoint, server_for(exception_map={TenantGone: _gone}), era)
    assert response.status_code == 200, response.content
    assert tool_error(response.json()["result"])["type"] == "tenant_gone"


@pytest.mark.parametrize("era", [LEGACY, MODERN])
async def test_each_view_still_answers_an_unmapped_exception_with_its_backstop(
    endpoint: Endpoint, era: str
) -> None:
    response = await _called(endpoint, _service_server(), era)
    assert response.status_code == 500, response.content
    assert response.json()["error"] == {"code": -32603, "message": "Internal error"}


def _gone_for_anything(exc: Exception) -> ToolResult:
    return build_error_tool_result(str(exc), error_type="anything")


@pytest.mark.parametrize("era", [LEGACY, MODERN])
@pytest.mark.parametrize("raised_by", ["service", "check"])
async def test_a_catch_all_entry_leaves_django_s_denial_a_403(
    endpoint: Endpoint, era: str, raised_by: str
) -> None:
    """The denial's own answer and challenge, rather than a ``200`` saying the tool ran."""

    def service() -> None:
        raise PermissionDenied("x")

    class Check(PermissionCheck):
        def has_permission(self, principal: Any, spec: Any) -> bool:
            raise PermissionDenied("x")

    spec = (
        ServiceSpec(service=service, permissions=[Unrestricted()])
        if raised_by == "service"
        else ServiceSpec(service=lambda: None, permissions=[Check()])
    )
    response = await _called(
        endpoint, _server(spec, exception_map={Exception: _gone_for_anything}), era
    )
    assert response.status_code == 403, response.content
    assert response.json()["error"]["code"] == -32006
    assert "WWW-Authenticate" in response


async def _streamed(server: MCPServer, era: str) -> Any:
    """``TOOL`` called on the async view with a ``progressToken``."""
    client = AsyncClient(raise_request_exception=False)
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=True)):
        session_id: str | None = None
        if era == LEGACY:
            init = await client.post(
                "/mcp/",
                data=initialize_body(),
                content_type="application/json",
            )
            session_id = init["Mcp-Session-Id"]
        return await client.post("/mcp/", **_request(era, session_id, progress=True))


async def _last_frame(response: Any) -> dict[str, Any]:
    raw = b"".join([chunk async for chunk in response.streaming_content]).decode()
    frames = [line[len("data: ") :] for line in raw.splitlines() if line.startswith("data: ")]
    return json.loads(frames[-1])


@pytest.mark.parametrize("era", [LEGACY, MODERN])
async def test_a_mapped_exception_from_a_check_is_answered_on_the_stream(era: str) -> None:
    """A ``progressToken`` moves the checks ahead of the stream."""
    response = await _streamed(_check_server(exception_map={TenantGone: _gone}), era)
    assert response.streaming
    assert tool_error((await _last_frame(response))["result"])["message"] == "from the check"


@pytest.mark.parametrize("era", [LEGACY, MODERN])
async def test_an_unmapped_exception_from_a_check_still_answers_before_the_stream(
    era: str,
) -> None:
    response = await _streamed(_check_server(), era)
    assert not response.streaming
    assert response.status_code == 500


@pytest.mark.parametrize("era", [LEGACY, MODERN])
async def test_a_mapped_exception_from_a_service_is_answered_on_the_stream(era: str) -> None:
    response = await _streamed(_service_server(exception_map={TenantGone: _gone}), era)
    assert response.streaming
    assert tool_error((await _last_frame(response))["result"])["message"] == "from the service"
