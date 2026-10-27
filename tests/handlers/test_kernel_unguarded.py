"""A kernel spec declaring no permissions, under each ``REQUIRE_TOOL_PERMISSIONS``.

Refused at registration with the setting on. With it off, registered with one
``UnguardedToolWarning`` and called without a permission check, under a grant
minted per call for that spec and principal; a guarded spec is never granted.
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, override_settings
from django_service_specs import (
    PermissionCheck,
    RegisteredSpec,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    SpecRegistry,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.server.utils import UnguardedToolWarning
from tests.kernel_client import KernelClient
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.urlconf_for import urlconf_for

pytestmark = pytest.mark.django_db(transaction=True)

_WORDING = (
    "its kernel spec declares no permissions (permissions=None). It is called "
    "without a permission check, because SPEC_MCP['REQUIRE_TOOL_PERMISSIONS'] is False."
)


class _Deny(PermissionCheck):
    def has_permission(self, *args: Any, **kwargs: Any) -> bool:
        return False


def _server(*, require: bool) -> MCPServer:
    return MCPServer(
        name="unguarded",
        auth_backend=HeaderAuthBackend(),
        config=replace(build_mcp_config(), require_tool_permissions=require),
    )


def _register(server: MCPServer) -> None:
    server.register_tool(
        ServiceSpec(service=lambda: {"ran": True}), name="open.write", description="Write."
    )
    server.register_resource(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: {"read": True}),
        name="open.read",
        uri_template="open://",
    )
    server.register_tool(
        ServiceSpec(service=lambda: {"ran": True}, permissions=[_Deny()]),
        name="closed.write",
        description="Write, denied.",
    )


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def caller(request: pytest.FixtureRequest, client: Client) -> Iterator[KernelClient]:
    server = _server(require=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UnguardedToolWarning)
        _register(server)
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=request.param)):
        kernel = KernelClient(client)
        kernel.initialize()
        yield kernel


def test_an_unguarded_kernel_tool_is_called_with_the_setting_off(caller: KernelClient) -> None:
    with warnings.catch_warnings():
        # Warned at registration only: a call emits nothing.
        warnings.simplefilter("error", UnguardedToolWarning)
        body = caller.call("open.write")
    assert body["result"]["structuredContent"] == {"ran": True}


def test_an_unguarded_kernel_resource_is_read_with_the_setting_off(caller: KernelClient) -> None:
    body = caller.post("resources/read", {"uri": "open://"}).json()
    assert "error" not in body
    assert json.loads(body["result"]["contents"][0]["text"]) == {"read": True}


def test_a_guarded_kernel_tool_still_refuses_with_the_setting_off(caller: KernelClient) -> None:
    body = caller.call("closed.write")
    assert (body["error"]["code"], body["error"]["message"]) == (-32006, "Insufficient permission")


def test_an_unguarded_kernel_tool_runs_in_process_with_the_setting_off() -> None:
    server = _server(require=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UnguardedToolWarning)
        _register(server)
    result = server.call_tool("open.write", {}, user=None)
    assert result.is_error is False


def test_a_spec_registry_cannot_carry_an_unguarded_spec_to_register_tools() -> None:
    # So the batch route never reaches the warning, and never earns the grant.
    with pytest.raises(ImproperlyConfigured, match="'open.batch' declares no permissions"):
        SpecRegistry(
            [RegisteredSpec(name="open.batch", spec=ServiceSpec(service=lambda: {"ran": True}))]
        )


def test_the_warning_fires_once_per_registration_in_kernel_terms() -> None:
    server = _server(require=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _register(server)
    unguarded = [w for w in caught if issubclass(w.category, UnguardedToolWarning)]
    assert [str(w.message) for w in unguarded] == [
        f"MCP tool 'open.write': {_WORDING} Declare permissions on the spec, "
        "[Unrestricted()] for an operation open to every caller.",
        f"MCP resource 'open.read': {_WORDING} Declare permissions on the spec, "
        "[Unrestricted()] for an operation open to every caller.",
    ]
    assert all("permission_classes" not in str(w.message) for w in unguarded)


@pytest.mark.parametrize("kind", ["tool", "resource"])
def test_the_setting_on_refuses_an_unguarded_kernel_spec(kind: str) -> None:
    server = _server(require=True)
    with pytest.raises(ImproperlyConfigured, match=r"declares no permissions \(permissions=None\)"):
        if kind == "tool":
            server.register_tool(ServiceSpec(service=lambda: None), name="open.write")
        else:
            server.register_resource(
                SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: None),
                name="open.read",
                uri_template="open://",
            )


def test_a_marked_binding_is_not_granted_by_a_server_with_the_setting_on(client: Client) -> None:
    ran: list[bool] = []
    lenient = _server(require=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UnguardedToolWarning)
        marked = lenient.register_tool(
            ServiceSpec(service=lambda: ran.append(True)), name="open.write", description="Write."
        )
    assert marked.unguarded is True
    strict = _server(require=True)
    strict.tools.register(marked)
    with pytest.raises(ImproperlyConfigured, match="declares no permissions"):
        strict.call_tool("open.write", {}, user=None)
    with override_settings(ROOT_URLCONF=urlconf_for(strict, is_async=False)):
        kernel = KernelClient(client)
        kernel.initialize()
        assert kernel.call("open.write")["error"]["code"] == -32603
    assert ran == []


@pytest.mark.parametrize("kind", ["tool", "resource"])
def test_the_unguarded_mark_cannot_be_set_at_construction(kind: str) -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument 'unguarded'"):
        if kind == "tool":
            KernelToolBinding(
                name="open.write",
                description="Write.",
                spec=ServiceSpec(service=lambda: None),
                **{"unguarded": True},
            )
        else:
            spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: None)
            KernelResourceBinding(
                name="open.read",
                uri_template="open://",
                description="Read.",
                selector=spec.selector,
                kind=SelectorKind.RETRIEVE,
                spec=spec,
                **{"unguarded": True},
            )
