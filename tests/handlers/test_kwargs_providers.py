"""A kernel spec's ``kwargs`` provider, on kernel tools and resources over both handlers.

Asks of specs registered with ``register_tool`` / ``register_resource`` what the
original, kept beside this file as ``test_kwargs_providers_drfs.py``, asks of
drf-services bindings: the provider's return reaches the callable as keywords,
on every call, beside a resource's URI-template variables; a provider declaring
``request`` is handed the call's request by name; a provider may query on the
async route; and a provider returning ``UNSET`` for a key declines it rather than
overriding the value the call carried, while one returning a value wins.

The kernel hands a provider what it declares from the pool, and the call's
``request`` and ``view`` (``CALL_SEEDS``) by name: the ``HttpRequest`` carrying
the caller's token as ``mcp_token``, and ``None`` for the view, where the
original handed a synthesised drf-services view and a DRF ``Request``.

Three questions of the original have no kernel counterpart. ``register_resource``
on a kernel spec takes no ``output_serializer``, since the presenter is the
spec's, so neither "the spec's serializer fills in" nor "the caller's wins"
applies; a kernel ``SelectorSpec`` refuses a missing selector when it is built,
before any registrar sees it; and the "render has the request" cases asked of a
serializer's context, which a kernel Presenter does not take.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.http import HttpRequest
from django_service_specs import (
    UNSET,
    DataclassPresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from tests.testapp.kernel_shapes import InvoiceOutput
from tests.testapp.models import Invoice

OPEN = [Unrestricted()]


class _IdentifiedUser(str):
    """A principal that is both a value and an identity, as the original's is."""

    pk = "alice"
    is_authenticated = True


_ALICE = _IdentifiedUser("alice")


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=_ALICE),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


TOOLS = pytest.mark.parametrize(
    "call",
    [handle_tools_call, async_to_sync(handle_tools_call_async)],
    ids=["sync", "async"],
)
READS = pytest.mark.parametrize(
    "read",
    [handle_resources_read, async_to_sync(handle_resources_read_async)],
    ids=["sync", "async"],
)


def _text(out: dict[str, Any]) -> Any:
    return json.loads(out["contents"][0]["text"])


# ---------- ServiceSpec.kwargs (tools/call) ----------


@TOOLS
def test_a_service_provider_is_merged_into_the_pool(call: Callable[..., Any]) -> None:
    """The provider's return reaches the service as keywords, and the provider
    is handed the call's request, carrying the caller, and no view."""
    seen: dict[str, Any] = {}

    def provider(*, request: Any, view: Any) -> dict[str, Any]:
        seen.update(user=request.mcp_token.user, view=view)
        return {"tenant_id": 42}

    server = _server()
    server.register_tool(
        ServiceSpec(
            service=lambda *, tenant_id: {"tenant_id": tenant_id},
            kwargs=provider,
            permissions=OPEN,
            atomic=False,
        ),
        name="t.x",
    )
    out = call({"name": "t.x", "arguments": {}}, _ctx(server))
    assert out["structuredContent"] == {"tenant_id": 42}
    assert seen == {"user": "alice", "view": None}


# ---------- SelectorSpec.kwargs (resources/read) ----------


def _resource(
    selector: Any, provider: Any, *, uri_template: str = "r://{pk}", **fields: Any
) -> tuple[MCPServer, Any]:
    server = _server()
    binding = server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=selector,
            reads=fields.pop("reads", Parameters([Parameter("pk", "string", required=True)])),
            kwargs=provider,
            permissions=OPEN,
            **fields,
        ),
        name="r",
        uri_template=uri_template,
    )
    return server, binding


def test_register_resource_takes_a_kernel_selector_spec_end_to_end() -> None:
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda *, pk: {"pk": pk, "via": "spec"},
        reads=Parameters([Parameter("pk", "string", required=True)]),
        permissions=OPEN,
    )
    server = _server()
    binding = server.register_resource(spec, name="r", uri_template="invoices://{pk}")
    assert isinstance(binding, KernelResourceBinding)
    assert binding.spec is spec
    assert _text(handle_resources_read({"uri": "invoices://3"}, _ctx(server))) == {
        "pk": "3",
        "via": "spec",
    }


@READS
def test_a_selector_provider_runs_on_every_read_beside_the_uri_variables(
    read: Callable[..., Any],
) -> None:
    calls: list[int] = []

    def provider(*, request: Any) -> dict[str, Any]:
        # Declaring ``request`` alone binds it by name, as on HTTP.
        calls.append(1)
        assert request.mcp_token.user == "alice"
        return {"tenant_id": 99}

    server, _ = _resource(lambda *, pk, tenant_id: {"pk": pk, "tenant": tenant_id}, provider)
    for _ in range(2):
        assert _text(read({"uri": "r://7"}, _ctx(server))) == {"pk": "7", "tenant": 99}
    assert calls == [1, 1]


@pytest.mark.django_db(transaction=True)
async def test_an_async_resource_provider_may_query() -> None:
    await sync_to_async(Invoice.objects.create)(number="A", amount_cents=1)
    server, _ = _resource(
        lambda *, pk, seen: {"pk": pk, "seen": seen}, lambda: {"seen": Invoice.objects.count()}
    )
    out = await handle_resources_read_async({"uri": "r://1"}, _ctx(server))
    assert _text(out) == {"pk": "1", "seen": 1}


@pytest.mark.django_db(transaction=True)
async def test_an_async_resource_presents_a_lazy_queryset_off_the_loop() -> None:
    invoice = await sync_to_async(Invoice.objects.create)(number="A", amount_cents=1)
    server, _ = _resource(
        lambda *, pk: Invoice.objects.filter(pk=pk),
        None,
        reads=Parameters([Parameter("pk", "integer", required=True)]),
        presenter=DataclassPresenter(InvoiceOutput),
    )
    out = await handle_resources_read_async({"uri": f"r://{invoice.pk}"}, _ctx(server))
    assert _text(out)["number"] == "A"


# ---------- the provider's UNSET decline contract ----------


@READS
@pytest.mark.parametrize(
    ("provided", "served"),
    [(UNSET, "42"), ("provider", "provider")],
    ids=["unset-declines", "a-value-wins"],
)
def test_a_provider_declines_with_unset_and_wins_with_a_value(
    read: Callable[..., Any], provided: Any, served: str
) -> None:
    """``UNSET`` means "I am not setting this key", so the URI's own value
    stands; handing the sentinel to the selector would turn a well-formed read
    into an internal error. A resolved value is still authoritative."""
    server, _ = _resource(
        lambda *, project_pk: {"project_pk": project_pk},
        lambda: {"project_pk": provided},
        uri_template="r://{project_pk}",
        reads=Parameters([Parameter("project_pk", "string", required=True)]),
    )
    assert _text(read({"uri": "r://42"}, _ctx(server))) == {"project_pk": served}
