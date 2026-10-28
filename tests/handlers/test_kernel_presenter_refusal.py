"""A presenter refusing a caller's shape is the caller's ``validation_error``.

A kernel spec's ``shapes`` are the one caller input read while the result is
presented rather than while the spec is dispatched, so a presenter refusing one
fails after ``dispatch`` has returned, outside every arm that decides whether a
failure is the caller's to fix. It escaped as a ``-32603`` on the wire and as a
raised ``ServiceValidationError`` from ``call_tool``. The drf-services path
answers the same failure of a read-shaping ``QueryParam`` with an ``isError``
``validation_error`` (``read_shaping_error_result``), and a kernel tool now
answers it alike, on every path a kernel tool is presented on: the wire, sync
and async, and the in-process ``call_tool``.

Only when the caller shaped the render: with no shape supplied, nothing the
model sends can change the outcome, so the refusal is a server bug and is raised
unchanged, as is anything a presenter raises that is not a validation error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django_service_specs import (
    DataclassPresenter,
    Parameter,
    Parameters,
    PresentationContext,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    ServiceValidationError,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.schema.types.agent_conventions import AgentConventions
from tests.utils import call_context, tool_error

REFUSAL = "No such field: total."


@dataclass
class _Row:
    id: int


class _StrictPresenter(DataclassPresenter):
    """Refuses any selection naming a field it lacks, as a strict renderer does."""

    def __init__(self, error: Exception | None = None) -> None:
        super().__init__(_Row)
        self.error = error

    def presented(self, value: Any, context: PresentationContext | None) -> Any:
        if self.error is not None:
            raise self.error
        raise ServiceValidationError(REFUSAL)


def _rows() -> list[_Row]:
    return [_Row(id=1), _Row(id=2)]


def _selector(
    *,
    error: Exception | None = None,
    shapes: tuple[str, ...] = ("fields",),
    nullable: bool = False,
) -> Any:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_rows,
        shapes=Parameters(
            [Parameter(name, "array", items="string", nullable=nullable) for name in shapes]
        ),
        presenter=_StrictPresenter(error),
        permissions=[Unrestricted()],
    )


def _server(**register: Any) -> MCPServer:
    conventions = register.pop("conventions", None)
    server = MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=None,
        **({"conventions": conventions} if conventions is not None else {}),
    )
    spec = register.pop("spec", None) or _selector()
    server.register_tool(spec, name="rows", description="d", **register)
    return server


def _wire(server: MCPServer, arguments: dict[str, Any], *, is_async: bool) -> Any:
    params = {"name": "rows", "arguments": arguments}
    context = call_context(server)
    if is_async:
        return async_to_sync(handle_tools_call_async)(params, context)
    return handle_tools_call(params, context)


_PATHS = pytest.mark.parametrize("path", ["sync", "async", "call_tool"])


def _call(server: MCPServer, arguments: dict[str, Any], path: str) -> dict[str, Any]:
    """The tool result ``path`` serves, as the wire's dict."""
    if path == "call_tool":
        return server.call_tool("rows", arguments, user=None).to_dict()
    return _wire(server, arguments, is_async=path == "async")


@_PATHS
def test_a_refused_shape_is_a_validation_error_keyed_by_it(path: str) -> None:
    error = tool_error(_call(_server(), {"fields": ["total"]}, path))
    assert error["type"] == "validation_error"
    assert error["message"] == f"`fields` was rejected while rendering the result: {REFUSAL}"
    assert error["detail"]["fields"] == [REFUSAL]


@_PATHS
def test_on_a_paged_tool_the_refusal_says_what_the_shape_applies_to(path: str) -> None:
    server = _server(paginate=True)
    error = tool_error(_call(server, {"fields": ["total"]}, path))
    scope = AgentConventions().query_param_on_pages
    assert scope is not None
    assert error["message"].endswith(f"{REFUSAL} {scope}")


@_PATHS
def test_several_shapes_supplied_are_named_together(path: str) -> None:
    server = _server(spec=_selector(shapes=("fields", "expand")))
    error = tool_error(_call(server, {"fields": ["id"], "expand": ["owner"]}, path))
    assert error["message"].startswith("`fields` or `expand` was rejected")
    assert error["detail"]["non_field_errors"] == [REFUSAL]


@_PATHS
def test_a_refusal_with_no_shape_supplied_is_raised(path: str) -> None:
    # Nothing the caller sent shaped the render, so it is the server's fault:
    # raised into ``call_tool`` and into the transport's backstop on the wire.
    with pytest.raises(ServiceValidationError):
        _call(_server(), {}, path)


@_PATHS
def test_an_explicit_null_is_not_supplied(path: str) -> None:
    # A shape declared nullable is the only one a ``null`` reaches the
    # presenter for; any other refuses it at the shape check.
    with pytest.raises(ServiceValidationError):
        _call(_server(spec=_selector(nullable=True)), {"fields": None}, path)


@_PATHS
def test_a_shape_the_server_binds_is_not_supplied(path: str) -> None:
    # The server's value is not the caller's, so a refusal of it is no more
    # the caller's to fix than a refusal with nothing sent.
    server = _server(server_arguments=ServerArguments(values={"fields": ["total"]}))
    with pytest.raises(ServiceValidationError):
        _call(server, {}, path)


@_PATHS
def test_a_presenter_error_that_is_not_a_validation_error_is_raised(path: str) -> None:
    server = _server(spec=_selector(error=ValueError(REFUSAL)))
    with pytest.raises(ValueError, match=REFUSAL):
        _call(server, {"fields": ["total"]}, path)
