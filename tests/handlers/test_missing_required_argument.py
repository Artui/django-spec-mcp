"""A kernel tool call that leaves out a required argument is a ``validation_error`` result.

The kernel refuses a missing argument itself: ``dispatch``'s shape check names
every required parameter the call left out, a selector's ``reads``, the
``reads`` of the selector a service resolves its target through, and a
Validator's fields alike, as ``InvalidArguments`` keyed by the name. The
transport answers that refusal as the validation ``isError`` on every route, so
the selector's ``TypeError`` the drf-services original guarded against, an HTTP
500 with ``-32603`` on the wire, has no way to arise. The original read this
off a selector's reflected signature and a pre-dispatch check of its own; it
is kept beside this file as ``test_missing_required_argument_drfs.py``.

Asked here as the original asked it: every route and both protocol eras answer
a result, the schema's ``required`` is what dispatch refuses, several missing
names are answered together, a provider or a registered seed filling a read
satisfies it, and a caller the tool is not for is refused for that before any
argument is read.

Changed on the kernel path, by the kernel's own rules:

- The message is ``"Invalid arguments"``, the words every ``InvalidArguments``
  is served in, with ``{"pk": ["This field is required."]}`` under ``detail``
  as before. The drf-services path's own sentence, ``"Missing required
  argument(s): `pk`."``, and the server's ``AgentConventions.missing_arguments``
  wording of it reach no kernel call: held as a strict ``xfail`` below.
- A Validator field and a target lookup's read left out together are both
  named, where the drf-services lookup check answered first and alone.
- A seed's name the client sends anyway is refused as an unknown argument: a
  ``reflected`` read leaves the argument set once a seed fills it, where the
  drf-services path admitted the name and let the seed outrank it.

Not asked, because the kernel has no counterpart: ``UrlKwarg`` and the
channel split that refused a missing one, a selector tool's MCP-only
``input_serializer`` (its own refusal first, its default filling a name),
``InputRequired``, ``NotClientInput`` and ``ArgumentBinding``. A kernel read is
declared on the spec, with its default, and a provider's keys are the ones its
return annotation names.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django.test import Client
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    UNSET,
    DataclassPresenter,
    DataclassValidator,
    NotPermitted,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
    UnsetType,
)
from typing_extensions import TypedDict

from django_spec_mcp import AgentConventions, MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from tests.conftest import post_jsonrpc
from tests.testapp.kernel_shapes import InvoiceOutput
from tests.testapp.models import Invoice
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import tool_error

pytestmark = pytest.mark.django_db(transaction=True)

MODERN = "2026-07-28"
OPEN = [Unrestricted()]
_REQUIRED = ["This field is required."]


@dataclass
class _Number:
    number: str


class _Deny(PermissionCheck):
    def has_permission(self, *args: Any, **kwargs: Any) -> bool:
        return False


class _Tenant(TypedDict):
    tenant: str


class _MaybeTenant(TypedDict):
    tenant: str | UnsetType


def _acme() -> _Tenant:
    return {"tenant": "acme"}


def _declines() -> _MaybeTenant:
    return {"tenant": UNSET}


def _by_pk(*, pk: int) -> Any:
    return Invoice.objects.filter(pk=pk)


def _by_ids(*, ids: list[int]) -> Any:
    return Invoice.objects.filter(pk__in=ids)


def _scoped(*, pk: int, tenant: str) -> dict[str, Any]:
    return {"pk": pk, "tenant": tenant}


def _rename(*, instance: Invoice, number: str) -> Invoice:
    instance.number = number
    instance.save(update_fields=["number"])
    return instance


def _rename_all(*, collection: Any, number: str) -> dict[str, int]:
    return {"renamed": collection.update(number=number)}


_PK = Parameter("pk", "integer", required=True)


def _get(*reads: Parameter, permissions: Any = OPEN) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_by_pk,
        reads=Parameters(list(reads) or [_PK]),
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=permissions,
    )


def _rename_spec(permissions: Any = OPEN) -> ServiceSpec:
    return ServiceSpec(
        service=_rename,
        atomic=False,
        validator=DataclassValidator(_Number),
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_by_pk, reads=Parameters([_PK])
        ),
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=permissions,
    )


def _server(*, permissions: Any = OPEN, **kwargs: Any) -> MCPServer:
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore(), **kwargs
    )
    server.register_tool(_get(permissions=permissions), name="get")
    server.register_tool(_rename_spec(permissions), name="rename")
    server.register_tool(
        ServiceSpec(
            service=_rename_all,
            atomic=False,
            validator=DataclassValidator(_Number),
            collection_selector_spec=SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_by_ids,
                reads=Parameters([Parameter("ids", "array", items="integer", required=True)]),
            ),
            permissions=permissions,
        ),
        name="rename_all",
    )
    return server


def _ctx(server: MCPServer, scopes: tuple[str, ...] = ()) -> MCPCallContext:
    # The server's seeds and wording, as the views hand them to the context
    # they build, so a handler route answers as the wire would.
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None, scopes=scopes),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        pool_seeds=server.pool_seeds,
        conventions=server.conventions,
    )


# Every entry point a tool call takes: the wire's sync and async handlers, and
# the two in-process ones, which reach dispatch by separate code.
_ROUTES = ["handler", "async_handler", "call_tool", "acall_tool"]


async def _via(server: MCPServer, route: str, name: str, arguments: dict[str, Any]) -> Any:
    """The result payload of ``name`` called with ``arguments`` through ``route``."""
    if route == "call_tool":
        return (await sync_to_async(server.call_tool)(name, arguments, user=None)).to_dict()
    if route == "acall_tool":
        return await server.acall_tool(name, arguments, user=None)
    params: dict[str, Any] = {"name": name, "arguments": arguments}
    if route == "async_handler":
        return await handle_tools_call_async(params, _ctx(server))
    # Off the event loop, where the sync handler's ORM work is allowed.
    return await sync_to_async(handle_tools_call)(params, _ctx(server))


def _refused(out: Any) -> dict[str, Any]:
    """The detail of a validation result, asserting it is one in the kernel path's words."""
    assert not isinstance(out, JsonRpcError), f"answered as a protocol error: {out!r}"
    error = tool_error(out)
    assert error["type"] == "validation_error"
    assert error["message"] == "Invalid arguments"
    return error["detail"]


# ---------- every route that answers a tool call ----------


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize(
    ("name", "arguments", "missing"),
    [
        # A selector tool's own read.
        ("get", {}, "pk"),
        # A service tool's instance lookup, beside a valid Validator field.
        ("rename", {"number": "INV-2"}, "pk"),
        # A service tool's collection lookup.
        ("rename_all", {"number": "INV-2"}, "ids"),
        # A service tool's Validator field, beside its lookup.
        ("rename", {"pk": 1}, "number"),
    ],
)
async def test_a_missing_argument_is_a_validation_error_result(
    route: str, name: str, arguments: dict[str, Any], missing: str
) -> None:
    out = await _via(_server(), route, name, arguments)

    assert _refused(out) == {missing: _REQUIRED}


def test_the_missing_argument_is_one_the_schema_requires() -> None:
    """The rule the client is told and the rule enforced are one."""
    listed: Any = _server().list_tools(user=None)
    schemas = {tool["name"]: tool["inputSchema"] for tool in listed["tools"]}

    assert schemas["get"]["required"] == ["pk"]
    assert set(schemas["rename"]["required"]) == {"pk", "number"}
    assert set(schemas["rename_all"]["required"]) == {"ids", "number"}


@pytest.mark.parametrize("route", _ROUTES)
async def test_several_missing_names_are_answered_together(route: str) -> None:
    # The lookup's read and the Validator's field, which the drf-services
    # path answered one check at a time, the lookup's alone first.
    out = await _via(_server(), route, "rename", {})

    assert _refused(out) == {"pk": _REQUIRED, "number": _REQUIRED}


# ---------- the wire, in both protocol eras ----------


def _post_modern(client: Client, params: dict[str, Any]) -> Any:
    meta: dict[str, Any] = {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0.0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    return client.post(
        "/mcp/",
        data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {**params, "_meta": meta}}
        ),
        content_type="application/json",
        headers={
            "Mcp-Protocol-Version": MODERN,
            "Mcp-Method": "tools/call",
            "Mcp-Name": params["name"],
        },
    )


@pytest.fixture
def mounted(settings: Any) -> None:
    """Mount this file's server; listed before ``era_session`` so ``initialize`` reaches it."""
    settings.ROOT_URLCONF = urlconf_for(_server())


@pytest.fixture(params=["2025-11-25", MODERN])
def era_session(request: Any, client: Client) -> str | None:
    if request.param == MODERN:
        return None
    return request.getfixturevalue("initialized_session")


def test_the_wire_serves_a_missing_argument_as_a_result(
    mounted: None, client: Client, era_session: str | None
) -> None:
    params: dict[str, Any] = {"name": "get", "arguments": {}}
    if era_session is None:
        response = _post_modern(client, params)
    else:
        response = post_jsonrpc(client, method="tools/call", params=params, session_id=era_session)

    assert response.status_code == 200, response.content
    body = response.json()
    assert "error" not in body, body
    assert _refused(body["result"]) == {"pk": _REQUIRED}


# ---------- after the permission answer ----------


@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize("name", ["get", "rename"])
async def test_a_caller_the_scope_gate_refuses_is_refused_before_the_argument_is_checked(
    name: str, is_async: bool
) -> None:
    # The gate is the transport's, judged before dispatch is reached, so a
    # caller without the scope is told only that.
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(_get(), name="get", scopes=("invoices:read",))
    server.register_tool(_rename_spec(), name="rename", scopes=("invoices:read",))
    params: dict[str, Any] = {"name": name, "arguments": {}}

    if is_async:
        out = await handle_tools_call_async(params, _ctx(server))
    else:
        out = await sync_to_async(handle_tools_call)(params, _ctx(server))
    granted = await handle_tools_call_async(params, _ctx(server, ("invoices:read",)))

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN
    # The other half: with the scope, the same call is answered for its argument.
    assert "pk" in _refused(granted)


@pytest.mark.parametrize("route", ["handler", "async_handler", "acall_tool"])
@pytest.mark.parametrize("name", ["get", "rename"])
async def test_a_caller_the_spec_denies_is_refused_before_the_argument_is_checked(
    route: str, name: str
) -> None:
    # A missing-argument answer would describe a tool the caller may not call.
    out = await _via(_server(permissions=[_Deny()]), route, name, {})

    assert isinstance(out, JsonRpcError), out
    assert out.code == JsonRpcErrorCode.FORBIDDEN


@pytest.mark.parametrize("name", ["get", "rename"])
def test_call_tool_refuses_a_caller_the_spec_denies_before_the_argument_is_checked(
    name: str,
) -> None:
    with pytest.raises(NotPermitted):
        _server(permissions=[_Deny()]).call_tool(name, {}, user=None)


# ---------- what fills a read ----------


def _scoped_server(provider: Any) -> MCPServer:
    """``get(*, pk, tenant)``, both reads required, with ``provider`` as its ``kwargs=``."""
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_scoped,
            reads=Parameters([_PK, Parameter("tenant", "string", required=True)]),
            kwargs=provider,
            permissions=OPEN,
        ),
        name="get",
    )
    return server


@pytest.mark.parametrize("route", _ROUTES)
async def test_a_read_the_provider_fills_is_not_asked_of_the_caller(route: str) -> None:
    server = _scoped_server(_acme)
    listed: Any = server.list_tools(user=None)

    out = await _via(server, route, "get", {"pk": 1})

    assert set(listed["tools"][0]["inputSchema"]["properties"]) == {"pk"}
    assert out["structuredContent"] == {"pk": 1, "tenant": "acme"}


@pytest.mark.parametrize("route", _ROUTES)
async def test_the_providers_value_outranks_the_callers(route: str) -> None:
    # Checked, then dropped: a ``tenant`` the caller sends never stands in for
    # the one the provider is annotated to fill.
    out = await _via(_scoped_server(_acme), route, "get", {"pk": 1, "tenant": "beta"})

    assert out["structuredContent"] == {"pk": 1, "tenant": "acme"}


@pytest.mark.parametrize("route", _ROUTES)
async def test_a_read_the_provider_declines_is_the_callers_to_send(route: str) -> None:
    out = await _via(_scoped_server(_declines), route, "get", {"pk": 1, "tenant": "beta"})

    assert out["structuredContent"] == {"pk": 1, "tenant": "beta"}


@pytest.mark.parametrize("route", _ROUTES)
async def test_a_read_the_provider_declines_and_the_caller_leaves_out_is_refused(
    route: str,
) -> None:
    # Only the provider's answer can say whether ``tenant`` arrived, so the
    # call is refused once it has answered, in the shape check's words.
    out = await _via(_scoped_server(_declines), route, "get", {"pk": 1})

    assert _refused(out) == {"tenant": _REQUIRED}


def _seeded_server() -> MCPServer:
    server = MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=None,
        pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"),
    )
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_scoped,
            reads=Parameters([_PK, Parameter("tenant", "string", required=True, reflected=True)]),
            permissions=OPEN,
        ),
        name="get",
    )
    return server


@pytest.mark.parametrize("route", _ROUTES)
async def test_a_reflected_read_a_registered_seed_fills_is_not_asked_of_the_caller(
    route: str,
) -> None:
    server = _seeded_server()
    listed: Any = server.list_tools(user=None)

    out = await _via(server, route, "get", {"pk": 1})

    assert listed["tools"][0]["inputSchema"]["required"] == ["pk"]
    assert out["structuredContent"] == {"pk": 1, "tenant": "acme"}


@pytest.mark.parametrize("route", _ROUTES)
async def test_a_seeds_name_the_client_sends_anyway_is_refused_as_unknown(route: str) -> None:
    # Changed by design: the seed fills the read, so the name is not an
    # argument of the call at all, and ``REJECT`` refuses it.
    out = await _via(_seeded_server(), route, "get", {"pk": 1, "tenant": "beta"})

    assert _refused(out) == {"tenant": ["Unknown argument."]}


# ---------- in the server's own words ----------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "A kernel InvalidArguments is answered 'Invalid arguments' whatever it refuses, so "
        "AgentConventions.missing_arguments never words a kernel call. Fixed by handing "
        "KernelRefusalScene the names the call left out of the spec's required parameters, "
        "from run_kernel_tool*.py and call_kernel_tool.py, for _invalid_arguments to word."
    ),
)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_missing_argument_is_worded_by_the_server(route: str) -> None:
    server = _server(conventions=AgentConventions(missing_arguments="Left out: {names}."))

    out = await _via(server, route, "get", {})

    error = tool_error(out)
    assert error["message"] == "Left out: `pk`."
    assert error["detail"] == {"pk": _REQUIRED}
