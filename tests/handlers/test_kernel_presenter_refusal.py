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
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.schema.types.agent_conventions import AgentConventions
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from tests.utils import tool_error

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
    # The server's own context, so its conventions word the refusal as they
    # would on the wire (``test_the_servers_conventions_word_the_sentence``).
    context = server._call_context(user=None)
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
    assert error == {
        "type": "validation_error",
        "message": f"`fields` was rejected while rendering the result: {REFUSAL}",
        "detail": {"fields": [REFUSAL]},
    }


@_PATHS
@pytest.mark.parametrize(
    ("detail", "readable"),
    [
        ({"total": ["Not a field."]}, "`total`: Not a field."),
        (["Not a field.", "Nor this."], "Not a field. Nor this."),
    ],
    ids=["dict", "list"],
)
def test_a_structured_refusal_is_worded_as_sentences(path: str, detail: Any, readable: str) -> None:
    # The message is read by a model, so a mapping or a list of errors is
    # spelled out rather than shown as its ``repr``; the ``detail`` keeps the
    # structure as raised.
    server = _server(spec=_selector(error=ServiceValidationError(detail)))
    error = tool_error(_call(server, {"fields": ["total"]}, path))
    assert error == {
        "type": "validation_error",
        "message": f"`fields` was rejected while rendering the result: {readable}",
        "detail": {"fields": detail},
    }


@_PATHS
def test_on_a_paged_tool_the_refusal_says_what_the_shape_applies_to(path: str) -> None:
    server = _server(paginate=True)
    error = tool_error(_call(server, {"fields": ["total"]}, path))
    scope = AgentConventions().query_param_on_pages
    assert scope is not None
    assert error == {
        "type": "validation_error",
        "message": f"`fields` was rejected while rendering the result: {REFUSAL} {scope}",
        "detail": {"fields": [REFUSAL]},
    }


@_PATHS
def test_the_servers_conventions_word_the_sentence(path: str) -> None:
    custom = AgentConventions(query_param_on_pages="Per row.")
    server = _server(paginate=True, conventions=custom)
    error = tool_error(_call(server, {"fields": ["total"]}, path))
    assert error == {
        "type": "validation_error",
        "message": f"`fields` was rejected while rendering the result: {REFUSAL} Per row.",
        "detail": {"fields": [REFUSAL]},
    }


@_PATHS
def test_none_drops_the_sentence(path: str) -> None:
    server = _server(paginate=True, conventions=AgentConventions(query_param_on_pages=None))
    error = tool_error(_call(server, {"fields": ["total"]}, path))
    assert error["message"] == f"`fields` was rejected while rendering the result: {REFUSAL}"


@_PATHS
def test_several_shapes_supplied_are_named_together(path: str) -> None:
    server = _server(spec=_selector(shapes=("fields", "expand")))
    error = tool_error(_call(server, {"fields": ["id"], "expand": ["owner"]}, path))
    assert error == {
        "type": "validation_error",
        "message": f"`fields` or `expand` was rejected while rendering the result: {REFUSAL}",
        "detail": {"non_field_errors": [REFUSAL]},
    }


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


# ----- a resource whose URI carries a shape -----


def _row() -> _Row:
    return _Row(id=1)


def _resource_server(
    uri_template: str,
    *,
    shapes: tuple[str, ...] = ("fields",),
    error: Exception | None = None,
) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_row,
            shapes=Parameters([Parameter(name, "string") for name in shapes]),
            presenter=_StrictPresenter(error),
            permissions=[Unrestricted()],
        ),
        name="row",
        uri_template=uri_template,
    )
    return server


_READS = pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])


def _read(server: MCPServer, uri: str, *, is_async: bool) -> Any:
    params = {"uri": uri}
    context = server._call_context(user=None)
    if is_async:
        return async_to_sync(handle_resources_read_async)(params, context)
    return handle_resources_read(params, context)


@_READS
def test_a_resource_refusing_a_shape_its_uri_carried_is_invalid_params(is_async: bool) -> None:
    # The ``-32602`` the shape check answers for the same variable, keyed by it.
    error = _read(_resource_server("rows://{fields}"), "rows://total", is_async=is_async)
    assert isinstance(error, JsonRpcError)
    assert (error.code, error.message) == (JsonRpcErrorCode.INVALID_PARAMS, "Invalid arguments")
    assert error.data == {"detail": {"fields": [REFUSAL]}}


@_READS
def test_a_resource_naming_several_shapes_keys_the_refusal_under_none_of_them(
    is_async: bool,
) -> None:
    server = _resource_server("rows://{fields}/{expand}", shapes=("fields", "expand"))
    error = _read(server, "rows://id/owner", is_async=is_async)
    assert isinstance(error, JsonRpcError)
    assert error.data == {"detail": {"non_field_errors": [REFUSAL]}}


@_READS
def test_a_resource_whose_uri_carries_no_shape_raises_the_refusal(is_async: bool) -> None:
    with pytest.raises(ServiceValidationError):
        _read(_resource_server("rows://all"), "rows://all", is_async=is_async)


@_READS
@pytest.mark.parametrize(
    "detail",
    [{"total": ["Not a field."]}, ["Not a field.", "Nor this."]],
    ids=["dict", "list"],
)
def test_a_resource_refusals_structured_detail_is_kept_as_raised(
    is_async: bool, detail: Any
) -> None:
    # A detail already structured is placed under the shape as it is; only a
    # bare message is wrapped in a list, as the shape check words its own.
    server = _resource_server("rows://{fields}", error=ServiceValidationError(detail))
    error = _read(server, "rows://total", is_async=is_async)
    assert isinstance(error, JsonRpcError)
    assert error.data == {"detail": {"fields": detail}}


@_READS
def test_a_resource_presenter_error_that_is_not_a_validation_error_is_raised(
    is_async: bool,
) -> None:
    server = _resource_server("rows://{fields}", error=ValueError("boom"))
    with pytest.raises(ValueError, match="boom"):
        _read(server, "rows://total", is_async=is_async)
