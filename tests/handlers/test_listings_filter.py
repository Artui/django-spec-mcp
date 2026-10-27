"""``FILTER_LISTINGS_BY_PERMISSIONS`` across the four list handlers, on kernel specs.

The kernel version of the drf-services original, kept beside it as
``test_listings_filter_drfs.py``. It asks what the original asked, of tools and
resources registered with ``register_tool`` and ``register_resource`` and guarded
by their specs' own ``PermissionCheck``s: every list handler (``tools/list``,
``resources/list``, ``resources/templates/list``, ``prompts/list``) with the
setting on and off, the ``always_listed`` opt-back-in, the filter running before
pagination, and a check scoped by what a call names being judged on nothing.

The original's route-scoped case asked a DRF permission reading a URL kwarg,
which the kernel has no counterpart for: a kernel call's route is in its
arguments. So here it is a check reading ``arguments``, which a listing hands as
``None``. Prompts are not specs on either path, so their two cases are the
original's. The async mount is new here: it runs the listing off the event loop,
so a check reading the database answers there too.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.http import HttpRequest
from django.test import Client, override_settings
from django_service_specs import (
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.handle_prompts_list import handle_prompts_list
from django_spec_mcp.handlers.handle_resources_list import handle_resources_list
from django_spec_mcp.handlers.handle_resources_templates_list import (
    handle_resources_templates_list,
)
from django_spec_mcp.handlers.handle_tools_list import handle_tools_list
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.registry.types.prompt_binding import PromptBinding
from tests.kernel_client import KernelClient
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.urlconf_for import urlconf_for

pytestmark = pytest.mark.django_db(transaction=True)


class _Refuse(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:  # noqa: ARG002
        return False


class _ForProject(PermissionCheck):
    """Admits a call naming project 7, recording the arguments it was handed."""

    def __init__(self) -> None:
        self.seen: list[Any] = []

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:  # noqa: ARG002
        self.seen.append(arguments)
        return arguments is not None and arguments.get("project_pk") == 7


class _HasPerm(PermissionCheck):
    """Reads the database, as ``user.has_perm`` does on its first call."""

    def has_permission(self, principal: Any, spec: Any) -> bool:  # noqa: ARG002
        return bool(principal.has_perm("auth.view_user"))


class _DenyPrompt:
    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:  # noqa: ARG002
        return False

    def required_scopes(self) -> list[str]:
        return []


def _write() -> dict[str, bool]:
    return {"ran": True}


def _one(pk: int = 0) -> dict[str, int]:
    return {"pk": pk}


def _render() -> str:
    return "x"


def _server(*, filtered: bool = True, page_size: int | None = None) -> MCPServer:
    config = replace(build_mcp_config(), filter_listings_by_permissions=filtered)
    if page_size is not None:
        config = replace(config, page_size=page_size)
    return MCPServer(name="filtered", auth_backend=HeaderAuthBackend(), config=config)


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        config=server.config,
    )


def _tool(server: MCPServer, name: str, *checks: PermissionCheck, **kwargs: Any) -> None:
    server.register_tool(
        ServiceSpec(service=_write, permissions=list(checks or [Unrestricted()])),
        name=name,
        **kwargs,
    )


def _resource(server: MCPServer, name: str, uri: str, *checks: PermissionCheck) -> None:
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_one, permissions=list(checks or [Unrestricted()])
        ),
        uri_template=uri,
        name=name,
    )


def _names(out: Any, key: str) -> list[str]:
    assert isinstance(out, dict)
    return [item["name"] for item in out[key]]


# ---------- tools/list ----------


def test_tools_list_setting_off_returns_all_tools_even_when_refused() -> None:
    server = _server(filtered=False)
    _tool(server, "a")
    _tool(server, "b", _Refuse())

    assert set(_names(handle_tools_list(None, _ctx(server)), "tools")) == {"a", "b"}


def test_tools_list_setting_on_hides_refused_tools() -> None:
    server = _server()
    _tool(server, "a")
    _tool(server, "b", _Refuse())

    assert set(_names(handle_tools_list(None, _ctx(server)), "tools")) == {"a"}


def test_tools_list_always_listed_overrides_a_refusal() -> None:
    server = _server()
    _tool(server, "a")
    _tool(server, "b", _Refuse(), always_listed=True)

    assert set(_names(handle_tools_list(None, _ctx(server)), "tools")) == {"a", "b"}


def test_tools_list_judges_an_argument_scoped_check_on_no_arguments() -> None:
    # A listing names no project, so a check admitting a call that names
    # project 7 is handed no arguments and hides the tool from a caller it admits
    # on that call; ``always_listed`` is the way back.
    check = _ForProject()
    server = _server()
    _tool(server, "a")
    _tool(server, "b", check)

    assert set(_names(handle_tools_list(None, _ctx(server)), "tools")) == {"a"}
    assert check.seen == [None]


def test_tools_list_filter_runs_before_pagination() -> None:
    """``nextCursor`` reflects the visible slice, not the whole registry."""
    server = _server(page_size=2)
    _tool(server, "refused-1", _Refuse())
    _tool(server, "refused-2", _Refuse())
    for name in ("visible-1", "visible-2", "visible-3"):
        _tool(server, name)

    out = handle_tools_list(None, _ctx(server))

    assert _names(out, "tools") == ["visible-1", "visible-2"]
    # Three visible at a page size of two: one more page.
    assert "nextCursor" in out


def test_the_async_mount_lists_by_a_check_that_reads_the_database(client: Client) -> None:
    user = get_user_model().objects.create(username="viewer")
    user.user_permissions.add(Permission.objects.get(codename="view_user"))
    get_user_model().objects.create(username="ann")
    server = _server()
    _tool(server, "a")
    _tool(server, "users", _HasPerm())

    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=True)):
        listed = {}
        for username in ("viewer", "ann"):
            kernel = KernelClient(client, user=username)
            kernel.initialize()
            listed[username] = set(_names(kernel.post("tools/list").json()["result"], "tools"))

    assert listed == {"viewer": {"a", "users"}, "ann": {"a"}}


# ---------- resources/list ----------


def test_resources_list_setting_on_hides_refused() -> None:
    server = _server()
    _resource(server, "a", "r://a")
    _resource(server, "b", "r://b", _Refuse())

    assert _names(handle_resources_list(None, _ctx(server)), "resources") == ["a"]


def test_resources_list_setting_off_returns_all() -> None:
    server = _server(filtered=False)
    _resource(server, "a", "r://a", _Refuse())

    assert _names(handle_resources_list(None, _ctx(server)), "resources") == ["a"]


# ---------- resources/templates/list ----------


def test_resources_templates_list_setting_on_hides_refused() -> None:
    server = _server()
    _resource(server, "a", "r://a/{pk}")
    _resource(server, "b", "r://b/{pk}", _Refuse())

    out = handle_resources_templates_list(None, _ctx(server))

    assert _names(out, "resourceTemplates") == ["a"]


def test_resources_templates_list_setting_off_returns_all() -> None:
    server = _server(filtered=False)
    _resource(server, "a", "r://a/{pk}", _Refuse())

    out = handle_resources_templates_list(None, _ctx(server))

    assert _names(out, "resourceTemplates") == ["a"]


# ---------- prompts/list ----------


def _prompt(server: MCPServer, name: str, *, permissions: tuple[Any, ...] = ()) -> None:
    server.prompts.register(
        PromptBinding(name=name, description=None, render=_render, permissions=permissions)
    )


def test_prompts_list_setting_on_hides_denied() -> None:
    server = _server()
    _prompt(server, "a")
    _prompt(server, "b", permissions=(_DenyPrompt(),))

    assert _names(handle_prompts_list(None, _ctx(server)), "prompts") == ["a"]


def test_prompts_list_setting_off_returns_all() -> None:
    server = _server(filtered=False)
    _prompt(server, "a", permissions=(_DenyPrompt(),))

    assert _names(handle_prompts_list(None, _ctx(server)), "prompts") == ["a"]
