"""A view resource: a ``ResourceBinding`` holding a bare selector, not a kernel spec.

UI resources are views (``ui_view_to_resource``), and so is a binding built by
hand. Their read, their listing and their watch run on the transport's own
checks alone, since there is no spec for the kernel to ask; these cases hold
each of those paths for a view.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.http import HttpRequest

from django_spec_mcp import SubscriptionFilter
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.is_binding_listable import is_binding_listable
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.prompt_registry import PromptRegistry
from django_spec_mcp.registry.resource_registry import ResourceRegistry
from django_spec_mcp.registry.tool_registry import ToolRegistry
from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.subscriptions.grant_subscription import grant_subscription
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode

_READS = pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])


class _Listable:
    """Refuses every call and answers the listing by ``listed``."""

    def __init__(self, *, listed: bool) -> None:
        self.listed = listed

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:  # noqa: ARG002
        return False

    def is_listable(self, token: TokenInfo) -> bool:  # noqa: ARG002
        return self.listed


def _view(selector: Any, *, permissions: tuple[Any, ...] = ()) -> ResourceBinding:
    return ResourceBinding(
        name="v",
        uri_template="v://page",
        description=None,
        selector=selector,
        permissions=permissions,
    )


def _context(binding: ResourceBinding, **config: Any) -> MCPCallContext:
    resources = ResourceRegistry()
    resources.register(binding)
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=ToolRegistry(),
        resources=resources,
        prompts=PromptRegistry(),
        protocol_version="2025-11-25",
        config=replace(build_mcp_config(), **config),
    )


def _read(context: MCPCallContext, *, is_async: bool) -> Any:
    params = {"uri": "v://page"}
    if is_async:
        return async_to_sync(handle_resources_read_async)(params, context)
    return handle_resources_read(params, context)


@_READS
def test_a_view_read_over_the_result_ceiling_is_a_server_error(is_async: bool) -> None:
    # A resource read has no ``isError`` shape, so the ceiling is answered as a
    # protocol error carrying the same remedy-naming message a tool's would.
    context = _context(_view(lambda: {"text": "x" * 100}), max_result_bytes=10)
    error = _read(context, is_async=is_async)
    assert isinstance(error, JsonRpcError)
    assert error.code == JsonRpcErrorCode.SERVER_ERROR
    assert "Resource 'v://page'" in error.message


def test_an_async_view_selector_is_awaited_on_the_async_read() -> None:
    async def page() -> dict[str, str]:
        return {"title": "async"}

    out = _read(_context(_view(page)), is_async=True)
    assert "async" in out["contents"][0]["text"]


@pytest.mark.parametrize("listed", [True, False])
def test_a_views_is_listable_answers_the_listing_in_place_of_has_permission(
    listed: bool,
) -> None:
    # ``has_permission`` refuses, so a listed view is listed by ``is_listable``
    # alone, and an unlisted one is hidden by it rather than by the call check.
    binding = _view(lambda: {}, permissions=(_Listable(listed=listed),))
    assert is_binding_listable(binding, HttpRequest(), TokenInfo(user=None)) is listed


def test_a_view_the_caller_may_read_is_watchable() -> None:
    # A view has no spec, so the binding's own checks are the whole answer.
    context = _context(_view(lambda: {}))
    granted, topics = grant_subscription(SubscriptionFilter(resource_uris=("v://page",)), context)
    assert granted.resource_uris == ("v://page",)
    assert len(topics) == 1
