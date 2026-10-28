"""An async read routed through drf-services touches the ORM off the event loop."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django_service_specs import SelectorKind

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore


def _usernames(**_: Any) -> Any:
    # ``values()`` is lazy: nothing queries until the encoder iterates it.
    return get_user_model().objects.values("username")


@pytest.mark.parametrize(
    ("selector", "kwargs_provider"),
    [
        pytest.param(lambda request: _usernames(), None, id="selector-taking-request"),
        pytest.param(lambda: _usernames(), lambda: {}, id="kwargs-provider"),
    ],
)
@pytest.mark.django_db(transaction=True)
async def test_an_async_drfs_read_of_a_lazy_queryset_encodes_off_the_loop(
    selector: Any, kwargs_provider: Any
) -> None:
    """With no output serializer the render hands the queryset on unevaluated,
    so the encoder is what queries; on the loop that raised
    ``SynchronousOnlyOperation``."""
    await get_user_model().objects.acreate(username="u1")
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    server.resources.register(
        ResourceBinding(
            name="r",
            uri_template="r://users",
            description=None,
            selector=selector,
            kind=SelectorKind.LIST,
            permissions=(),
            kwargs_provider=kwargs_provider,
        )
    )
    context = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )

    out: Any = await handle_resources_read_async({"uri": "r://users"}, context)

    # Encoded as its text, which evaluating the queryset is what produces.
    assert "'username': 'u1'" in out["contents"][0]["text"]
