"""Kernel bindings built by hand and passed straight to a registry.

``server.tools.register`` and ``server.resources.register`` take a binding as
built, so nothing checks its spec's guard the way ``register_tool`` and
``register_resource`` do. Such a binding is never called without a permission
check, whatever ``REQUIRE_TOOL_PERMISSIONS`` says: only the registrar's warning
earns an unguarded spec its grant, and without one the kernel's dispatch
refuses the spec. A hand-built resource binding's ``scopes`` gate the read on
their own, as a tool binding's do.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, override_settings
from django_service_specs import SelectorKind, SelectorSpec, ServiceSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from tests.kernel_client import KernelClient
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.urlconf_for import urlconf_for

pytestmark = pytest.mark.django_db(transaction=True)

RAN: list[str] = []

# The kernel's dispatch refusal of a spec declaring no permissions, with no grant.
_REFUSED = "declares no permissions"


def _write() -> dict[str, bool]:
    RAN.append("write")
    return {"ran": True}


def _read() -> dict[str, bool]:
    RAN.append("read")
    return {"read": True}


@pytest.fixture(autouse=True)
def _reset() -> None:
    RAN.clear()


def _server(*, require: bool) -> MCPServer:
    return MCPServer(
        name="handbuilt",
        auth_backend=HeaderAuthBackend(),
        config=replace(build_mcp_config(), require_tool_permissions=require),
    )


def _tool(permissions: Any) -> KernelToolBinding:
    return KernelToolBinding(
        name="hand.write",
        description="Write.",
        spec=ServiceSpec(service=_write, permissions=permissions),
    )


def _resource(permissions: Any, *, scopes: Any = None) -> KernelResourceBinding:
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_read, permissions=permissions)
    return KernelResourceBinding(
        name="hand.read",
        uri_template="hand://",
        description="Read.",
        selector=spec.selector,
        kind=spec.kind,
        spec=spec,
        scopes=scopes,
    )


def _call_tool(client: Client, server: MCPServer, via: str) -> dict[str, Any]:
    """The tool call's result, over the sync or async wire or in process."""
    if via == "in-process":
        result = server.call_tool("hand.write", {}, user=None)
        return {"isError": result.is_error, "content": result.content}
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=via == "async")):
        kernel = KernelClient(client)
        kernel.initialize()
        return kernel.call("hand.write")


def _read_resource(
    client: Client, server: MCPServer, *, is_async: bool, scopes: str = ""
) -> dict[str, Any]:
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        kernel = KernelClient(client, scopes=scopes)
        kernel.initialize()
        return kernel.post("resources/read", {"uri": "hand://"}).json()


@pytest.mark.parametrize("require", [True, False], ids=["setting-on", "setting-off"])
@pytest.mark.parametrize("via", ["sync", "async", "in-process"])
def test_a_handbuilt_unguarded_kernel_tool_is_never_called(
    client: Client, require: bool, via: str
) -> None:
    server = _server(require=require)
    server.tools.register(_tool(None))
    if via == "in-process":
        # In process the kernel's refusal of an unguarded spec reaches the caller.
        with pytest.raises(ImproperlyConfigured, match=_REFUSED):
            _call_tool(client, server, via)
    else:
        # On the wire it is a server misconfiguration, so an internal error.
        assert _call_tool(client, server, via)["error"]["code"] == -32603
    assert RAN == []


@pytest.mark.parametrize("require", [True, False], ids=["setting-on", "setting-off"])
@pytest.mark.parametrize("via", ["sync", "async", "in-process"])
def test_a_handbuilt_guarded_kernel_tool_is_called(client: Client, require: bool, via: str) -> None:
    # The control for the refusal above: the same binding over an open spec runs,
    # so what stops the unguarded one is its missing guard and nothing upstream.
    server = _server(require=require)
    server.tools.register(_tool([Unrestricted()]))
    body = _call_tool(client, server, via)
    assert RAN == ["write"], body


@pytest.mark.parametrize("require", [True, False], ids=["setting-on", "setting-off"])
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_a_handbuilt_unguarded_kernel_resource_is_never_read(
    client: Client, require: bool, is_async: bool
) -> None:
    server = _server(require=require)
    server.resources.register(_resource(None))
    assert _read_resource(client, server, is_async=is_async)["error"]["code"] == -32603
    assert RAN == []


@pytest.mark.parametrize("require", [True, False], ids=["setting-on", "setting-off"])
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_a_handbuilt_guarded_kernel_resource_is_read(
    client: Client, require: bool, is_async: bool
) -> None:
    server = _server(require=require)
    server.resources.register(_resource([Unrestricted()]))
    body = _read_resource(client, server, is_async=is_async)
    assert RAN == ["read"], body


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_a_handbuilt_kernel_resource_gates_on_its_scopes(client: Client, is_async: bool) -> None:
    server = _server(require=True)
    server.resources.register(_resource([Unrestricted()], scopes=("billing",)))
    body = _read_resource(client, server, is_async=is_async)
    assert body["error"]["code"] == -32006, body
    assert RAN == []
    # A token carrying the scope is let through, so the refusal was the gate's.
    body = _read_resource(client, server, is_async=is_async, scopes="billing")
    assert RAN == ["read"], body


def test_a_handbuilt_kernel_resource_reads_a_bare_string_scope_as_one() -> None:
    binding = _resource([Unrestricted()], scopes="billing")
    assert binding.scopes == ("billing",)
    (gate,) = binding.permissions
    assert gate.required_scopes() == ["billing"]


def test_a_handbuilt_kernel_resource_without_scopes_has_no_gate() -> None:
    assert _resource([Unrestricted()]).permissions == ()


@pytest.mark.parametrize("build", [_tool, _resource], ids=["tool", "resource"])
def test_a_handbuilt_kernel_binding_refuses_an_empty_scope_tuple(build: Any) -> None:
    def build_with_no_scopes() -> Any:
        if build is _tool:
            return KernelToolBinding(
                name="hand.write",
                description="Write.",
                spec=ServiceSpec(service=_write, permissions=[Unrestricted()]),
                scopes=(),
            )
        return _resource([Unrestricted()], scopes=())

    with pytest.raises(ImproperlyConfigured, match=r"scopes=\(\) permits every caller"):
        build_with_no_scopes()
