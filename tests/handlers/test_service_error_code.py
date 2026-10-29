"""A kernel refusal's ``code`` reaches the caller, on every route a kernel tool is called by.

Asks of tools registered with ``register_tool``:
``ActionUnavailable(reason, code=...)`` serves its ``code`` beside the reason,
because the reason is a sentence that gets reworded and the code is the name a
client switches on, while a plain ``ServiceConflict`` serves no ``code`` key at
all, not a null one.

A kernel tool has three arms: ``run_kernel_tool`` on the sync
wire, ``run_kernel_tool_async`` on the async one and ``call_kernel_tool`` in
process, and a service and a selector take the same arm, so each arm's test is
parametrised over the two. A kernel selector's ``affordances`` name the operations
on its row rather than refusing the read, so a precondition raising the refusal is
how one reaches a selector tool.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import async_to_sync
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
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from tests.utils import call_context, tool_error

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
    assert (
        tool_error(handle_tools_call({"name": "t", "arguments": {}}, call_context(server)))
        == served
    )


@CASES
def test_the_async_wire_serves_the_code(kind: str, outcome: str, served: dict[str, Any]) -> None:
    server = _server(kind, outcome)
    out = async_to_sync(handle_tools_call_async)(
        {"name": "t", "arguments": {}}, call_context(server)
    )
    assert tool_error(out) == served


@CASES
def test_call_tool_serves_the_code(kind: str, outcome: str, served: dict[str, Any]) -> None:
    server = _server(kind, outcome)
    assert tool_error(server.call_tool("t", user=None).to_dict()) == served
