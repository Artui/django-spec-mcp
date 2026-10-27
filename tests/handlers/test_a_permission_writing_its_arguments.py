"""A permission writing into its ``arguments`` reaches neither the next check, the service nor the caller.

The kernel's answer to ``test_a_permission_writing_request_data_drfs.py``, kept
beside it. The original asked it of ``request.data``: a drf-services permission
stamping a value into it could carry that value into the dispatch view's check
and into the caller's own mapping. The kernel has no request mapping to write
into. A check that declares ``arguments`` is handed the call's shape-checked
arguments as a read-only mapping, a copy of the top level behind a proxy, so:

- a write raises ``TypeError`` at the check, which is a fault of the check and
  not a refusal: the next check and the service never run, and the handler
  lets it through as it does any fault, so the client reads the transport's
  internal error with none of the check's wording;
- a check that swallows that ``TypeError`` still changed nothing: the next
  check and the service are handed the arguments the caller sent, and the
  caller's own dict is untouched;
- the way to supply a value the service takes is a ``kwargs`` provider, whose
  return reaches the service as keywords.

The original asked a service tool and a selector tool; so does this, each kind
parametrised over the fault and the swallowed write. The ``kwargs`` provider
case is asked of the service alone.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.http import HttpRequest
from django.test import Client, override_settings
from django_service_specs import (
    DataclassValidator,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.types.internal_error_message import INTERNAL_ERROR_MESSAGE
from tests.kernel_client import KernelClient
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.urlconf_for import urlconf_for


@dataclass
class _Note:
    note: str = ""
    # Declared, with a default, so the service takes ``owner`` as a keyword
    # whether a provider supplies it or nobody does.
    owner: str = ""


class _Stamps(PermissionCheck):
    """Tries to stamp ``owner`` into its ``arguments``, and either lets the fault out or swallows it."""

    def __init__(self, *, swallow: bool) -> None:
        self.swallow = swallow

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:
        try:
            arguments["owner"] = "stamped"
        except TypeError:
            if not self.swallow:
                raise
        return True


class _Records(PermissionCheck):
    """The next check: records what it is handed."""

    def __init__(self, saw: list[dict[str, Any]]) -> None:
        self.saw = saw

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:
        self.saw.append(dict(arguments))
        return True


def _server(
    *,
    swallow: bool = False,
    kind: str = "service",
    provider: Callable[..., dict[str, Any]] | None = None,
    auth_backend: Any = None,
) -> tuple[MCPServer, list[dict[str, Any]], list[dict[str, Any]]]:
    """``tool`` behind a stamping check then a recording one, with what each saw."""
    checked: list[dict[str, Any]] = []
    ran: list[dict[str, Any]] = []

    def service(*, note: str, owner: str = "", **_pool: Any) -> dict[str, Any]:
        # The pool's own keywords (the caller, progress) are not the question.
        ran.append({"note": note, "owner": owner})
        return {"ran": True}

    server = MCPServer(name="t", auth_backend=auth_backend or AllowAnyBackend(), session_store=None)
    permissions: list[PermissionCheck] = [_Records(checked)]
    if provider is None:
        permissions.insert(0, _Stamps(swallow=swallow))
    spec: ServiceSpec | SelectorSpec
    if kind == "service":
        spec = ServiceSpec(
            service=service,
            validator=DataclassValidator(_Note),
            kwargs=provider,
            permissions=permissions,
            atomic=False,
        )
    else:
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=service,
            reads=Parameters([Parameter("note", "string"), Parameter("owner", "string")]),
            kwargs=provider,
            permissions=permissions,
        )
    server.register_tool(spec, name="tool")
    return server, checked, ran


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


KINDS = pytest.mark.parametrize("kind", ["service", "selector"])
CALLS = pytest.mark.parametrize(
    "call",
    [handle_tools_call, async_to_sync(handle_tools_call_async)],
    ids=["sync", "async"],
)


@KINDS
@CALLS
def test_a_write_into_arguments_is_a_fault_that_stops_the_call(
    call: Callable[..., Any], kind: str
) -> None:
    server, checked, ran = _server(kind=kind)
    arguments = {"note": "x"}
    with pytest.raises(TypeError, match="item assignment"):
        call({"name": "tool", "arguments": arguments}, _ctx(server))
    # The fault is the first check's: the second never ran, nor the service.
    assert (checked, ran) == ([], [])
    assert arguments == {"note": "x"}


@KINDS
@CALLS
def test_a_swallowed_write_changes_nothing_downstream(call: Callable[..., Any], kind: str) -> None:
    """The proxy, not the raise, is what holds: with the fault swallowed the call
    runs, and nothing past the writing check finds its stamp."""
    server, checked, ran = _server(swallow=True, kind=kind)
    arguments = {"note": "x"}
    out = call({"name": "tool", "arguments": arguments}, _ctx(server))
    assert out.get("isError") is not True, out
    assert checked == [{"note": "x"}]
    # ``owner`` is the declared default (the dataclass's, the selector's):
    # nobody stamped it.
    assert ran == [{"note": "x", "owner": ""}]
    assert arguments == {"note": "x"}


@CALLS
def test_a_kwargs_provider_is_how_a_value_reaches_the_service(call: Callable[..., Any]) -> None:
    server, checked, ran = _server(provider=lambda: {"owner": "stamped"})
    arguments = {"note": "x"}
    out = call({"name": "tool", "arguments": arguments}, _ctx(server))
    assert out.get("isError") is not True, out
    assert ran == [{"note": "x", "owner": "stamped"}]
    # The provider supplies the service's keyword; it is not an argument the
    # caller sent, so the checks are not handed it, and nor is the caller's dict.
    assert checked == [{"note": "x"}]
    assert arguments == {"note": "x"}


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def mount(request: pytest.FixtureRequest) -> Iterator[None]:
    server, _checked, _ran = _server(auth_backend=HeaderAuthBackend())
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=request.param)):
        yield


def test_the_client_reads_the_internal_error_and_none_of_the_fault(
    mount: None, client: Client
) -> None:
    caller = KernelClient(client)
    caller.initialize()
    body = caller.call("tool", {"note": "x"})
    assert body["error"] == {"code": -32603, "message": INTERNAL_ERROR_MESSAGE}
