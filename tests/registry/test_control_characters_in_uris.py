"""A control character is not part of a ``{var}`` value; such a URI is unknown.

Each ``{var}`` matches one or more characters other than ``/`` and other than
control characters, so ``invoices://7\\n`` fits no template, and neither does
``reports://\\n`` fit the concrete ``reports://`` binding (the URIs are not
equal). A read is refused as an unknown resource (``-32602`` under
``2026-07-28``, ``-32002`` under ``2025-11-25``), on the sync and the async
path alike, and a subscription leaves the URI out of its grant.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django_service_specs import (
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer, SubscriptionFilter
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.resource_registry import ResourceRegistry
from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.subscriptions.grant_subscription import grant_subscription
from django_spec_mcp.subscriptions.utils import topic_for_resource
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from tests.utils import call_context, plain_context

CONTROL_URIS = [
    "reports://\n",
    "invoices://7\n",
    "invoices://\n7",
    "invoices://7\t",
    "invoices://\x00",
]


def _invoice(*, pk: str) -> dict[str, str]:
    return {"pk": pk}


def _report() -> dict[str, str]:
    return {"report": "all"}


def _one_report(*, report_id: str) -> dict[str, str]:
    return {"report": report_id}


def _server() -> MCPServer:
    server = MCPServer(name="uris", auth_backend=AllowAnyBackend())
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_invoice,
            reads=Parameters([Parameter("pk", "string", required=True)]),
            permissions=[Unrestricted()],
        ),
        name="invoice",
        uri_template="invoices://{pk}",
    )
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_report,
            reads=Parameters([]),
            permissions=[Unrestricted()],
        ),
        name="report",
        uri_template="reports://",
    )
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_one_report,
            reads=Parameters([Parameter("report_id", "string", required=True)]),
            permissions=[Unrestricted()],
        ),
        name="one_report",
        uri_template="reports://{report_id}",
    )
    return server


def _assert_unknown(out: Any, uri: str) -> None:
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.INVALID_PARAMS
    assert out.message == f"Unknown resource: {uri!r}"
    assert out.data == {"uri": uri}


@pytest.mark.parametrize("uri", CONTROL_URIS)
def test_a_sync_read_of_a_uri_with_a_control_character_is_unknown(uri: str) -> None:
    out = handle_resources_read(
        {"uri": uri}, plain_context(_server(), protocol_version="2026-07-28")
    )
    _assert_unknown(out, uri)


@pytest.mark.parametrize("uri", CONTROL_URIS)
async def test_an_async_read_of_a_uri_with_a_control_character_is_unknown(uri: str) -> None:
    out = await handle_resources_read_async(
        {"uri": uri}, plain_context(_server(), protocol_version="2026-07-28")
    )
    _assert_unknown(out, uri)


def test_in_the_older_era_the_same_read_is_the_not_found_code() -> None:
    """``-32002`` under ``2025-11-25``, as for any unknown URI."""
    out = handle_resources_read(
        {"uri": "reports://\n"}, plain_context(_server(), protocol_version="2025-11-25")
    )
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.RESOURCE_NOT_FOUND
    assert out.message == "Unknown resource: 'reports://\\n'"


@pytest.mark.parametrize("uri", CONTROL_URIS)
def test_a_subscription_drops_a_uri_with_a_control_character(uri: str) -> None:
    granted, topics = grant_subscription(
        SubscriptionFilter(resource_uris=(uri, "invoices://7")), call_context(_server())
    )
    assert granted.resource_uris == ("invoices://7",)
    assert topics == frozenset({topic_for_resource("invoices://7")})


def test_a_read_without_one_still_resolves() -> None:
    out = handle_resources_read({"uri": "invoices://7"}, plain_context(_server()))
    assert not isinstance(out, JsonRpcError)
    assert '"pk": "7"' in out["contents"][0]["text"]
    assert async_to_sync(handle_resources_read_async)(
        {"uri": "reports://"}, plain_context(_server())
    )


def _binding(uri_template: str) -> ResourceBinding:
    return ResourceBinding(
        name="r", uri_template=uri_template, description=None, selector=lambda: None
    )


@pytest.mark.parametrize("char", ["\x00", "\n", "\r", "\t", "\x1f", "\x7f", "\x85"])
def test_resolve_refuses_a_control_character_wherever_it_falls(char: str) -> None:
    reg = ResourceRegistry()
    reg.register(_binding("invoices://{pk}"))
    reg.register(_binding("reports://"))
    reg.register(_binding("reports://{report_id}"))
    assert reg.resolve(f"invoices://{char}") is None
    assert reg.resolve(f"invoices://7{char}") is None
    assert reg.resolve(f"invoices://{char}7") is None
    assert reg.resolve(f"reports://{char}") is None
    assert reg.resolve(f"reports://{char}x") is None


def test_resolve_still_carries_every_printable_character() -> None:
    reg = ResourceRegistry()
    template = _binding("invoices://{pk}")
    reg.register(template)
    assert reg.resolve("invoices://a b%20é-_.~") == (template, {"pk": "a b%20é-_.~"})
