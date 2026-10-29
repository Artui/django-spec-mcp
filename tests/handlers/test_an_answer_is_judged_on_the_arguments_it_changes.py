"""A retry's answer to a kernel service tool is judged on the arguments it produces.

``tools/call`` merges a retry's ``inputResponses`` over the arguments as sent,
even with no ``requestState``, so a first call can carry one. A kernel spec's
permissions are its own ``PermissionCheck``s, and one declaring an
``arguments`` keyword is handed the call's arguments by ``dispatch``, which the
transport reaches once, with the merged arguments. So a check admitting only
project ``"7"`` refuses ``"8"`` whether it was sent plainly or answered over an
admitted ``"7"``, and admits ``"7"`` answered over a refused ``"8"``, on either
handler, either view and over a stream.

A kernel check reads the arguments rather than a request, so there is nothing
to judge before the merge for an answer to slip past.

A caller the spec refuses is not charged the rate limit, answered or not: the
shell asks a kernel spec's checks before the charge (``admit_kernel_call``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django.test import AsyncClient, Client, override_settings
from django_service_specs import DataclassValidator, PermissionCheck, ServiceSpec

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.types.elicitation_key import ELICITATION_KEY
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import RefusingRateLimit

pytestmark = pytest.mark.django_db(transaction=True)

_MODERN = "2026-07-28"
_ASYNC = pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])


@dataclass
class _Project:
    project: str


class _OnlyProject7(PermissionCheck):
    """Admits a call whose arguments name project 7, read as dispatch hands them."""

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:
        return arguments.get("project") == "7"


def _server(ran: list[Any], *, limiter: Any = None) -> MCPServer:
    def _service(*, data: _Project) -> dict[str, bool]:
        ran.append(data.project)
        return {"ok": True}

    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    server.register_tool(
        ServiceSpec(
            service=_service,
            validator=DataclassValidator(_Project),
            atomic=False,
            permissions=[_OnlyProject7()],
        ),
        name="tool",
        description="Touch a project.",
        rate_limits=[limiter] if limiter is not None else None,
    )
    return server


def _params(arguments: dict[str, Any], answer: dict[str, Any] | None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "name": "tool",
        "arguments": arguments,
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": _MODERN,
            "io.modelcontextprotocol/clientCapabilities": {},
        },
    }
    if answer is not None:
        params["inputResponses"] = {ELICITATION_KEY: {"action": "accept", "content": answer}}
    return params


def _body(params: dict[str, Any]) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})


_HEADERS = {"Mcp-Protocol-Version": _MODERN, "Mcp-Method": "tools/call", "Mcp-Name": "tool"}


async def _post(server: MCPServer, params: dict[str, Any], *, is_async: bool) -> Any:
    """The status and JSON-RPC envelope a wire ``tools/call`` answers."""
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        if is_async:
            response = await AsyncClient().post(
                "/mcp/", data=_body(params), content_type="application/json", headers=_HEADERS
            )
        else:
            response = await sync_to_async(Client().post)(
                "/mcp/", data=_body(params), content_type="application/json", headers=_HEADERS
            )
    return response.status_code, json.loads(response.content)


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version=_MODERN,
    )


async def _handle(server: MCPServer, params: dict[str, Any], *, is_async: bool) -> Any:
    if is_async:
        return await handle_tools_call_async(params, _ctx(server))
    return await sync_to_async(handle_tools_call)(params, _ctx(server))


@_ASYNC
async def test_an_answer_changing_a_value_the_permission_reads_is_refused(
    is_async: bool,
) -> None:
    ran: list[Any] = []
    server = _server(ran)

    # Sent plainly, the answered value is refused.
    plain = await _post(server, _params({"project": "8"}, None), is_async=is_async)
    # Answered over an admitted value, it is refused the same way.
    status, envelope = await _post(
        server, _params({"project": "7"}, {"project": "8"}), is_async=is_async
    )

    assert plain[0] == 403
    assert plain[1]["error"]["code"] == JsonRpcErrorCode.FORBIDDEN
    assert status == 403, envelope
    assert envelope["error"]["code"] == JsonRpcErrorCode.FORBIDDEN
    assert ran == []


@_ASYNC
async def test_an_answer_naming_the_admitted_value_runs_on_it(is_async: bool) -> None:
    # The other half: the check reads the merged arguments rather than
    # refusing every answered call, so the test above holds what it was shown.
    ran: list[Any] = []

    out = await _handle(
        _server(ran), _params({"project": "8"}, {"project": "7"}), is_async=is_async
    )

    assert out["structuredContent"] == {"ok": True}
    assert ran == ["7"]


async def test_a_streamed_answer_changing_a_value_the_permission_reads_is_refused() -> None:
    ran: list[Any] = []
    params = _params({"project": "7"}, {"project": "8"})
    params["_meta"]["progressToken"] = "abc"

    with override_settings(ROOT_URLCONF=urlconf_for(_server(ran), is_async=True)):
        response = await AsyncClient().post(
            "/mcp/", data=_body(params), content_type="application/json", headers=_HEADERS
        )
        assert response["Content-Type"].startswith("text/event-stream"), response.content[:300]
        body = b"".join([chunk async for chunk in response]).decode()

    frames = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert "error" in frames[-1], f"served {frames[-1]!r}"
    assert frames[-1]["error"]["code"] == JsonRpcErrorCode.FORBIDDEN
    assert ran == []


@pytest.mark.parametrize(
    ("sent", "answer"),
    [({"project": "7"}, {"project": "8"}), ({"project": "8"}, None)],
    ids=["answered", "plain"],
)
@_ASYNC
async def test_a_caller_the_spec_refuses_is_not_charged(
    sent: dict[str, Any], answer: dict[str, Any] | None, is_async: bool
) -> None:
    limit = RefusingRateLimit()
    ran: list[Any] = []

    out = await _handle(_server(ran, limiter=limit), _params(sent, answer), is_async=is_async)

    assert isinstance(out, JsonRpcError) and out.code == JsonRpcErrorCode.FORBIDDEN, out
    assert limit.consumed == 0
    assert ran == []
