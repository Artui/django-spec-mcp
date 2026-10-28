"""A mounted kernel server that can push notifications, for the end-to-end suite.

``open`` and ``gated`` are kernel
``RETRIEVE`` resources, and ``gated``'s spec refuses every principal with its
own ``PermissionCheck``, which is the gate a kernel read answers to. Module-level
so the broker outlives a single request, as it does in a deployment: a
subscription carries an event a different request produced.
"""

from __future__ import annotations

from typing import Any

from django.urls import path
from django_service_specs import (
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore

BROKER = InMemorySubscriptionBroker()


class _Deny(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:
        return False


def _thing(*, pk: int) -> dict[str, int]:
    return {"pk": pk}


def thing_spec(*permissions: PermissionCheck) -> SelectorSpec:
    """A kernel resource reading ``pk`` off its URI, admitting every caller by default."""
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_thing,
        reads=Parameters([Parameter("pk", "integer", required=True)]),
        permissions=list(permissions) or [Unrestricted()],
    )


def _build() -> MCPServer:
    server = MCPServer(
        name="subs-e2e-kernel",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        subscription_broker=BROKER,
    )
    server.register_resource(thing_spec(), name="open", uri_template="open://{pk}")
    server.register_resource(thing_spec(_Deny()), name="gated", uri_template="gated://{pk}")
    return server


SERVER = _build()

urlpatterns: list[Any] = [path("mcp/", SERVER.async_urls)]
