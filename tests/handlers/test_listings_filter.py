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

Two further questions of a kernel binding are new here. A check that reads
``arguments`` without guarding for a listing's ``None`` raises, which hides its
binding alone, on every list handler and both mounts, and ``always_listed`` keeps
it visible. And a create-shaped service spec, one with no target selector, is
asked its scope check of ``None`` as every call asks it, so a check refusing
``None`` hides it; a service on a row or a set and a selector spec are not.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.test import Client, override_settings
from django_service_specs import (
    DataclassValidator,
    Parameter,
    Parameters,
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


class _NeedsArgument(PermissionCheck):
    """Admits a call whose ``x`` is 1, and raises ``TypeError`` on a listing's ``None``.

    The intolerant twin of ``_ForProject``: it reads ``arguments`` without
    guarding for the absent mapping a listing hands, as a check written for the
    call alone does.
    """

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:  # noqa: ARG002
        return bool(arguments["x"] == 1)


class _ScopeRefusesNone(PermissionCheck):
    """Admits every class-level question and refuses a scope of ``None``.

    ``None`` is the scope of a call acting on no existing row, which is what a
    create asks, so a call to a create-shaped spec under this check is refused.
    """

    def has_permission(self, principal: Any, spec: Any) -> bool:  # noqa: ARG002
        return True

    def has_scope_permission(self, principal: Any, spec: Any, scope: Any) -> bool:  # noqa: ARG002
        return scope is not None


class _ScopeAdmitsNone(PermissionCheck):
    """Admits a scope of ``None`` and records the ``call_context`` it was handed."""

    def __init__(self) -> None:
        self.seen: list[Any] = []

    def has_permission(self, principal: Any, spec: Any) -> bool:  # noqa: ARG002
        return True

    def has_scope_permission(
        self,
        principal: Any,  # noqa: ARG002
        spec: Any,  # noqa: ARG002
        scope: Any,
        *,
        call_context: dict[str, Any],
    ) -> bool:
        self.seen.append(call_context)
        return scope is None


class _AwaitsAnswer(PermissionCheck):
    """A plain ``def`` answering an awaitable, which the kernel refuses where it reads it."""

    def has_permission(self, principal: Any, spec: Any) -> Any:  # noqa: ARG002
        async def verdict() -> bool:
            return True

        return verdict()


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
            kind=SelectorKind.RETRIEVE,
            selector=_one,
            reads=Parameters([Parameter("pk", "integer")]),
            permissions=list(checks or [Unrestricted()]),
        ),
        uri_template=uri,
        name=name,
    )


@dataclass
class _XInput:
    x: int = 0


def _accept(**_: Any) -> dict[str, bool]:
    return {"ran": True}


def _takes_x(server: MCPServer, name: str, *checks: PermissionCheck, **kwargs: Any) -> None:
    """A tool taking an ``x`` argument, guarded by ``checks``."""
    server.register_tool(
        ServiceSpec(
            service=_accept, validator=DataclassValidator(_XInput), permissions=list(checks)
        ),
        name=name,
        **kwargs,
    )


def _listed_over_http(
    client: Client, server: MCPServer, method: str, key: str, *, is_async: bool
) -> list[str]:
    """The names ``method`` answers over a mount, asserting it answered at all."""
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        kernel = KernelClient(client)
        kernel.initialize()
        response = kernel.post(method)
    assert response.status_code == 200, response.content
    return _names(response.json()["result"], key)


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


# ---------- a check that reads the call's arguments ----------

both_mounts = pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])


@both_mounts
def test_a_check_reading_arguments_hides_its_tool_and_no_other(
    client: Client, is_async: bool
) -> None:
    # A listing hands a check no arguments, so one reading them raises. That
    # hides its own tool, as the docs promise of a check that cannot decide at
    # list time, rather than failing the whole listing (HTTP 500) and taking an
    # unrestricted tool with it.
    server = _server()
    _tool(server, "open")
    _takes_x(server, "needs-x", _NeedsArgument())

    names = _listed_over_http(client, server, "tools/list", "tools", is_async=is_async)

    assert names == ["open"]


@both_mounts
def test_a_resource_check_reading_arguments_hides_its_resource_and_no_other(
    client: Client, is_async: bool
) -> None:
    server = _server()
    _resource(server, "open", "r://open")
    _resource(server, "needs-x", "r://needs-x", _NeedsArgument())

    names = _listed_over_http(client, server, "resources/list", "resources", is_async=is_async)

    assert names == ["open"]


@both_mounts
def test_a_template_check_reading_arguments_hides_its_template_and_no_other(
    client: Client, is_async: bool
) -> None:
    server = _server()
    _resource(server, "open", "r://open/{pk}")
    _resource(server, "needs-x", "r://needs-x/{pk}", _NeedsArgument())

    names = _listed_over_http(
        client, server, "resources/templates/list", "resourceTemplates", is_async=is_async
    )

    assert names == ["open"]


@both_mounts
def test_always_listed_keeps_a_tool_whose_check_reads_arguments_visible(
    client: Client, is_async: bool
) -> None:
    server = _server()
    _tool(server, "open")
    _takes_x(server, "needs-x", _NeedsArgument(), always_listed=True)

    names = _listed_over_http(client, server, "tools/list", "tools", is_async=is_async)

    assert names == ["open", "needs-x"]


@both_mounts
def test_a_call_naming_the_argument_succeeds_where_the_listing_hides_the_tool(
    client: Client, is_async: bool
) -> None:
    server = _server()
    _takes_x(server, "needs-x", _NeedsArgument())

    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        kernel = KernelClient(client)
        kernel.initialize()
        admitted = kernel.call("needs-x", {"x": 1})
        refused = kernel.call("needs-x", {"x": 2})

    assert admitted["result"].get("isError", False) is False
    assert refused["error"]["code"] == -32006


def test_a_check_left_awaiting_its_answer_still_fails_the_listing() -> None:
    # ``ImproperlyConfigured`` is a configuration error the call path raises as
    # well, so the listing does not hide the binding for it: without the
    # re-raise the broad arm below it would, and this would list nothing and
    # raise nothing.
    server = _server()
    _tool(server, "awaits", _AwaitsAnswer())

    with pytest.raises(ImproperlyConfigured):
        handle_tools_list(None, _ctx(server))


@both_mounts
def test_a_check_left_awaiting_its_answer_fails_the_call_too(
    client: Client, is_async: bool
) -> None:
    # The claim the listing's re-raise rests on: the call path does not answer
    # this configuration error either, so a listing that hid the binding for it
    # would be the one place it went unreported.
    server = _server(filtered=False)
    _tool(server, "awaits", _AwaitsAnswer())

    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        kernel = KernelClient(client)
        kernel.initialize()
        response = kernel.post("tools/call", {"name": "awaits", "arguments": {}})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == -32603


# ---------- the scope question of a create ----------


def _create_tool(server: MCPServer, name: str, *checks: PermissionCheck) -> None:
    server.register_tool(ServiceSpec(service=_write, permissions=list(checks)), name=name)


def test_a_create_whose_scope_check_refuses_nothing_is_hidden() -> None:
    # Dispatch asks a create's scope check of ``None`` on every call, reading
    # no row and no arguments, so a caller it refuses is answered an error at
    # every call and is listed nothing.
    server = _server()
    _tool(server, "open")
    _create_tool(server, "create", _ScopeRefusesNone())

    assert _names(handle_tools_list(None, _ctx(server)), "tools") == ["open"]


def test_a_create_whose_scope_check_admits_nothing_is_listed() -> None:
    check = _ScopeAdmitsNone()
    server = _server()
    _create_tool(server, "create", check)

    assert _names(handle_tools_list(None, _ctx(server)), "tools") == ["create"]
    # The scope question is handed what a call hands it: the tool's name as
    # the action, and a request carrying the token.
    assert [seen["action"] for seen in check.seen] == ["create"]
    assert hasattr(check.seen[0]["request"], "mcp_token")


@both_mounts
def test_a_listed_create_is_one_whose_call_is_not_refused_for_its_scope(
    client: Client, is_async: bool
) -> None:
    server = _server()
    _create_tool(server, "refused", _ScopeRefusesNone())
    _create_tool(server, "admitted", _ScopeAdmitsNone())
    unfiltered = _server(filtered=False)
    _create_tool(unfiltered, "refused", _ScopeRefusesNone())
    _create_tool(unfiltered, "admitted", _ScopeAdmitsNone())

    listed = _listed_over_http(client, server, "tools/list", "tools", is_async=is_async)
    with override_settings(ROOT_URLCONF=urlconf_for(unfiltered, is_async=is_async)):
        kernel = KernelClient(client)
        kernel.initialize()
        answers = {name: kernel.call(name) for name in ("refused", "admitted")}

    assert listed == ["admitted"]
    assert answers["refused"]["error"]["code"] == -32006
    assert answers["admitted"]["result"].get("isError", False) is False


def _one_row() -> dict[str, int]:
    return {"pk": 1}


def _rows() -> list[dict[str, int]]:
    return [{"pk": 1}]


@pytest.mark.parametrize(
    "selectors",
    [
        {
            "instance_selector_spec": SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_one_row, permissions=[Unrestricted()]
            )
        },
        {
            "collection_selector_spec": SelectorSpec(
                kind=SelectorKind.LIST, selector=_rows, permissions=[Unrestricted()]
            )
        },
    ],
    ids=["instance", "collection"],
)
def test_a_service_with_a_target_is_not_asked_the_scope_of_nothing(
    selectors: dict[str, SelectorSpec],
) -> None:
    # Held by the target-selector conjunct of the create shape: a write on a
    # row asks ``has_object_permission`` of the row it finds and a bulk write
    # asks ``has_scope_permission`` of the set, so neither is asked of ``None``
    # and this refusal of ``None`` is no reason to hide the tool.
    server = _server()
    server.register_tool(
        ServiceSpec(service=_write, permissions=[_ScopeRefusesNone()], **selectors),
        name="targeted",
    )

    assert _names(handle_tools_list(None, _ctx(server)), "tools") == ["targeted"]


def test_a_selector_tool_is_not_asked_the_scope_of_nothing() -> None:
    # Held by the kind conjunct of the create shape: a selector spec has no
    # target selector to be missing, and its scope question is of the set it
    # reads.
    server = _server()
    server.register_tool(
        SelectorSpec(kind=SelectorKind.LIST, selector=_rows, permissions=[_ScopeRefusesNone()]),
        name="rows",
    )

    assert _names(handle_tools_list(None, _ctx(server)), "tools") == ["rows"]
