"""The names an ``AdditionalInputRequired`` may ask under are the ones dispatch takes.

A Validator may declare ``parameters(self, *, call_context=None)``, and the
kernel's dispatch validates a call against the declaration its own
``call_context`` gives. The answer to a question comes back as an argument,
so a question for a name that declaration leaves out could never be answered:
it is refused as a malformed question, ``-32603``, rather than asked.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django_service_specs import (
    AdditionalInputRequired,
    DataclassValidator,
    Parameters,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from tests.elicitation.conftest import TOOL, DeleteRows, context
from tests.utils import tool_error


class _NoReasonInACall(DataclassValidator):
    """Declares ``reason`` to a reader outside a call, and leaves it out of every call."""

    def parameters(self, *, call_context: dict[str, Any] | None = None) -> Parameters:
        declared = super().parameters()
        if call_context is None:
            return declared
        return Parameters([each for each in declared if each.name != "reason"])


async def _call(params: dict[str, Any], ctx: MCPCallContext, is_async: bool) -> Any:
    if is_async:
        return await handle_tools_call_async(params, ctx)
    return await sync_to_async(handle_tools_call)(params, ctx)


def _asks_for_a_reason(*, count: int, confirmed: bool = False) -> dict[str, Any]:  # noqa: ARG001
    raise AdditionalInputRequired("Why?", schema={"reason": {"type": "string"}})


def _server() -> MCPServer:
    server = MCPServer(name="narrowing", auth_backend=AllowAnyBackend())
    server.register_tool(
        ServiceSpec(
            service=_asks_for_a_reason,
            validator=_NoReasonInACall(DeleteRows),
            permissions=[Unrestricted()],
            atomic=False,
        ),
        name=TOOL,
    )
    return server


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_dispatch_refuses_a_name_the_call_s_declaration_leaves_out(is_async: bool) -> None:
    """The premise: dispatch takes no ``reason`` inside a call."""
    server = _server()
    out = await _call(
        {"name": TOOL, "arguments": {"count": 1, "reason": "x"}}, context(server), is_async
    )
    assert "reason" in str(tool_error(out)), out


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_a_question_for_a_name_the_call_s_declaration_leaves_out_is_an_internal_error(
    is_async: bool,
) -> None:
    """Both halves hand ``tool_refusal_scene`` the admission's ``call_context``;
    handed ``None`` the question would be checked against the wider declaration
    and asked, so each half is pinned by this case."""
    server = _server()
    out = await _call({"name": TOOL, "arguments": {"count": 1}}, context(server), is_async)
    assert isinstance(out, JsonRpcError), out
    assert out.code == -32603
    assert "'reason'" in out.message
