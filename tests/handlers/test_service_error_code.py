"""A kernel refusal's ``code`` reaches the caller, on every route a kernel tool is called by.

Asks of tools registered with ``register_tool`` what the original, kept beside
this file as ``test_service_error_code_drfs.py``, asks of drf-services bindings:
``ActionUnavailable(reason, code=...)`` serves its ``code`` beside the reason,
because the reason is a sentence that gets reworded and the code is the name a
client switches on, while a plain ``ServiceConflict`` serves no ``code`` key at
all, not a null one.

The original held one test per arm, six of them, because each drf-services path
built its own result. A kernel tool has three: ``run_kernel_tool`` on the sync
wire, ``run_kernel_tool_async`` on the async one and ``call_kernel_tool`` in
process, and a service and a selector take the same arm, so each arm's test is
parametrised over the two. A kernel selector's ``affordances`` name the operations
on its row rather than refusing the read, so a precondition raising the refusal is
how one reaches a selector tool, as in the original.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.http import HttpRequest
from django_service_specs import (
    ActionUnavailable,
    Affordance,
    SelectorKind,
    SelectorSpec,
    ServiceConflict,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from tests.utils import tool_error

OPEN = [Unrestricted()]

_REFUSED: dict[str, Any] = {
    "type": "service_error",
    "message": "The books are closed.",
    "code": "books_closed",
}
"""What a refusal serves: the ``type`` and ``message``, and the code."""

_CONFLICT: dict[str, Any] = {"type": "service_error", "message": "That slot is taken."}
"""What a plain ``ServiceConflict`` serves: no ``code`` key at all, not a null one."""


def _closed() -> list[Affordance]:
    return [Affordance(code="books_closed", reason="The books are closed.", when=lambda: False)]


def _raise_refusal() -> None:
    raise ActionUnavailable("The books are closed.", code="books_closed")


def _raise_conflict() -> None:
    raise ServiceConflict("That slot is taken.")


def _spec(kind: str, outcome: str) -> Any:
    """A service refused by an affordance, a selector by a precondition, or either conflicting."""
    conflict: dict[str, Any] = {"preconditions": [_raise_conflict]}
    if kind == "service":
        return ServiceSpec(
            service=lambda: {"status": "ran"},
            permissions=OPEN,
            atomic=False,
            **({"affordances": _closed()} if outcome == "refused" else conflict),
        )
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda: {"number": "A-1"},
        permissions=OPEN,
        **({"preconditions": [_raise_refusal]} if outcome == "refused" else conflict),
    )


def _server(kind: str, outcome: str) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(_spec(kind, outcome), name="t")
    return server


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


CASES = pytest.mark.parametrize(
    ("kind", "outcome", "served"),
    [
        ("service", "refused", _REFUSED),
        ("service", "conflict", _CONFLICT),
        ("selector", "refused", _REFUSED),
        ("selector", "conflict", _CONFLICT),
    ],
    ids=["service-refused", "service-conflict", "selector-refused", "selector-conflict"],
)


@CASES
def test_the_sync_wire_serves_the_code(kind: str, outcome: str, served: dict[str, Any]) -> None:
    server = _server(kind, outcome)
    assert tool_error(handle_tools_call({"name": "t", "arguments": {}}, _ctx(server))) == served


@CASES
def test_the_async_wire_serves_the_code(kind: str, outcome: str, served: dict[str, Any]) -> None:
    server = _server(kind, outcome)
    out = async_to_sync(handle_tools_call_async)({"name": "t", "arguments": {}}, _ctx(server))
    assert tool_error(out) == served


@CASES
def test_call_tool_serves_the_code(kind: str, outcome: str, served: dict[str, Any]) -> None:
    server = _server(kind, outcome)
    assert tool_error(server.call_tool("t", user=None).to_dict()) == served
