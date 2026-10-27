"""End-to-end ``register_tools`` parity through the live transport, on kernel specs.

Tools registered in bulk from a ``SpecRegistry`` must show up in ``tools/list``
and answer ``tools/call`` exactly as a ``register_tool`` call of the same spec
would; ``tests/server/test_register_kernel_specs.py`` pins the bindings field for
field, and this file proves the wire shape lands too. The spec's own
permissions, not a binding's, are what gate a bulk-registered tool.

The drf-services original asked this of the conformance mount's
``ToolDefinition`` entries and is kept beside this file as
``test_register_tools_parity_drfs.py``. This version mounts its own server,
because the conformance mount registers drf-services specs.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from django.test import Client, override_settings
from django_service_specs import (
    DataclassPresenter,
    PermissionCheck,
    RegisteredSpec,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    SpecRegistry,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from tests.kernel_client import KernelClient
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.urlconf_for import urlconf_for


@dataclass
class _Sentinel:
    sentinel: str


class _Deny(PermissionCheck):
    def has_permission(self, *args: Any, **kwargs: Any) -> bool:
        return False


def _bulk_list() -> list[_Sentinel]:
    return [_Sentinel(sentinel="bulk")]


def _build() -> MCPServer:
    server = MCPServer(name="parity", auth_backend=HeaderAuthBackend())
    registry = SpecRegistry(
        [
            RegisteredSpec(
                name="conformance.bulk_listed",
                spec=SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=_bulk_list,
                    presenter=DataclassPresenter(_Sentinel),
                    permissions=[Unrestricted()],
                ),
            ),
            RegisteredSpec(
                name="conformance.bulk_gated",
                spec=ServiceSpec(service=lambda: None, permissions=[_Deny()]),
            ),
        ]
    )
    server.register_tools(
        registry,
        overrides={
            "conformance.bulk_listed": {
                "description": "Registered via register_tools()",
                "paginate": True,
            },
            "conformance.bulk_gated": {"description": "Denied by its spec."},
        },
    )
    return server


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def caller(request: pytest.FixtureRequest, client: Client) -> Iterator[KernelClient]:
    with override_settings(ROOT_URLCONF=urlconf_for(_build(), is_async=request.param)):
        kernel = KernelClient(client)
        kernel.initialize()
        yield kernel


@pytest.mark.django_db(transaction=True)
def test_bulk_registered_tools_visible_in_tools_list(caller: KernelClient) -> None:
    response = caller.post("tools/list", {})
    assert response.status_code == 200, response.content
    tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
    assert set(tools) == {"conformance.bulk_listed", "conformance.bulk_gated"}
    assert tools["conformance.bulk_listed"]["description"] == "Registered via register_tools()"
    assert {"page", "limit"} <= set(tools["conformance.bulk_listed"]["inputSchema"]["properties"])


@pytest.mark.django_db(transaction=True)
def test_bulk_registered_selector_callable(caller: KernelClient) -> None:
    body = caller.call("conformance.bulk_listed")
    assert body["result"]["structuredContent"]["items"] == [{"sentinel": "bulk"}]


@pytest.mark.django_db(transaction=True)
def test_bulk_registered_service_is_gated_by_its_specs_permissions(
    caller: KernelClient,
) -> None:
    response = caller.post("tools/call", {"name": "conformance.bulk_gated", "arguments": {}})
    # 403 per the MCP authorization spec's error table: "invalid scopes or
    # insufficient permissions". The JSON-RPC error rides in the body too.
    assert response.status_code == 403, response.content
    assert response.json()["error"]["code"] == -32006  # FORBIDDEN
