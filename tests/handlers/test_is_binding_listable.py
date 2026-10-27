"""Whether a kernel binding is listed to a caller, under ``FILTER_LISTINGS_BY_PERMISSIONS``.

The kernel version of the drf-services original, kept beside it as
``test_is_binding_listable_drfs.py``. The original asked the helper of
hand-built drfs bindings whose ``permissions`` are the transport's own checks:
every check admitting lists the binding, any refusal hides it, the first refusal
decides, ``always_listed`` opts back in, a check's ``is_listable(token)`` answers
in place of ``has_permission``, and the selector-tool and resource bindings are
judged the same way.

Here the bindings are kernel ones, registered with ``register_tool`` and
``register_resource``. Their ``permissions`` field holds only the scope gate
(``scopes=``); the spec's own ``PermissionCheck``s are the kernel's, and the
listing asks them through the kernel's ``authorize`` with what a call asks them
with: the caller as principal, the server's pool seeds, and a call context whose
request carries the token. So a tool is hidden from exactly the callers its call
would refuse before resolving anything, as the drfs path hides one whose
permission classes refuse.

Not carried: ``is_listable(token)``. A kernel ``PermissionCheck`` has no
list-time method, and a listing hands it no arguments (``None``, the kernel's
word for "none to hand"), so a check that decides by the call's arguments
decides on none; ``always_listed`` is the way back, as it is for the drfs path.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.handle_resources_list import handle_resources_list
from django_spec_mcp.handlers.handle_resources_templates_list import (
    handle_resources_templates_list,
)
from django_spec_mcp.handlers.handle_tools_list import handle_tools_list
from django_spec_mcp.handlers.is_binding_listable import is_binding_listable
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.server.utils import UnguardedToolWarning
from tests.testapp.auth_backend import HeaderAuthBackend

pytestmark = pytest.mark.django_db


class _IsStaff(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:  # noqa: ARG002
        return bool(getattr(principal, "is_staff", False))


class _Refuse(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:  # noqa: ARG002
        return False


class _Recording(PermissionCheck):
    """Admits everyone, recording what the listing handed it."""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    def has_permission(
        self,
        principal: Any,
        spec: Any,  # noqa: ARG002
        *,
        arguments: Any = "unset",
        call_context: Any = None,
        tenant: Any = "unset",
    ) -> bool:
        self.seen.append(
            {
                "principal": principal,
                "arguments": arguments,
                "call_context": call_context,
                "tenant": tenant,
            }
        )
        return True


def _write() -> dict[str, bool]:
    return {"ran": True}


def _one(pk: int) -> dict[str, int]:
    return {"pk": pk}


def _rows() -> list[dict[str, int]]:
    return []


def _server(**kwargs: Any) -> MCPServer:
    return MCPServer(
        name="listable",
        auth_backend=HeaderAuthBackend(),
        # The test settings turn the guard requirement off; a server here keeps
        # the default, so only the registrar's mark grants an unguarded spec.
        config=replace(
            build_mcp_config(), filter_listings_by_permissions=True, require_tool_permissions=True
        ),
        **kwargs,
    )


def _service(*checks: PermissionCheck) -> ServiceSpec:
    return ServiceSpec(service=_write, permissions=list(checks))


def _listed(server: MCPServer, user: Any, *, scopes: tuple[str, ...] = ()) -> set[str]:
    out = server.list_tools(user=user, scopes=scopes)
    assert isinstance(out, dict)
    return {t["name"] for t in out["tools"]}


def _user(username: str, **fields: Any) -> Any:
    return get_user_model().objects.create(username=username, **fields)


def test_a_tool_whose_checks_admit_the_caller_is_listed() -> None:
    server = _server()
    server.register_tool(_service(Unrestricted()), name="open")

    assert _listed(server, _user("ann")) == {"open"}


def test_a_tool_is_hidden_from_the_caller_its_spec_s_checks_refuse() -> None:
    # Both directions on one tool: a test asserting only "hidden" would also
    # pass were every tool hidden.
    server = _server()
    server.register_tool(_service(Unrestricted()), name="open")
    server.register_tool(_service(_IsStaff()), name="staff_only")

    assert _listed(server, _user("staff", is_staff=True)) == {"open", "staff_only"}
    assert _listed(server, _user("ann")) == {"open"}


def test_every_check_must_admit_the_caller() -> None:
    server = _server()
    server.register_tool(_service(Unrestricted(), _Refuse(), Unrestricted()), name="t")

    assert _listed(server, _user("ann")) == set()


def test_a_scope_held_does_not_list_a_tool_the_spec_refuses() -> None:
    # The scope gate passes and the spec's check refuses: the listing asks both.
    server = _server()
    server.register_tool(_service(_IsStaff()), name="t", scopes=("invoices",))

    assert _listed(server, _user("ann"), scopes=("invoices",)) == set()


def test_a_spec_that_admits_does_not_list_a_tool_whose_scope_is_missing() -> None:
    # The spec's check admits and the scope gate refuses: still hidden.
    server = _server()
    server.register_tool(_service(_IsStaff()), name="t", scopes=("invoices",))
    staff = _user("staff", is_staff=True)

    assert _listed(server, staff) == set()
    assert _listed(server, staff, scopes=("invoices",)) == {"t"}


def test_always_listed_lists_a_tool_the_spec_refuses() -> None:
    server = _server()
    server.register_tool(_service(_Refuse()), name="t", always_listed=True)

    assert _listed(server, _user("ann")) == {"t"}


def test_a_deactivated_caller_is_listed_nothing() -> None:
    # Dispatch refuses a deactivated principal whatever its checks say, so the
    # listing does too.
    server = _server()
    server.register_tool(_service(Unrestricted()), name="open")

    assert _listed(server, _user("gone", is_active=False)) == set()


def test_the_checks_are_asked_with_what_a_call_asks_them_with() -> None:
    check = _Recording()
    server = _server(pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"))
    server.register_tool(_service(check), name="t")
    ann = _user("ann")

    assert _listed(server, ann, scopes=("invoices",)) == {"t"}

    [seen] = check.seen
    assert seen["principal"] == ann
    # A listing has no arguments to hand, and says so the kernel's way.
    assert seen["arguments"] is None
    # The server's pool seeds are offered, as dispatch offers them.
    assert seen["tenant"] == "acme"
    # The token rides on the request, where a check reading a claim finds it.
    assert seen["call_context"]["action"] == "t"
    assert seen["call_context"]["request"].mcp_token.scopes == ("invoices",)


def test_an_anonymous_caller_is_asked_about_as_an_anonymous_user() -> None:
    check = _Recording()
    server = _server()
    server.register_tool(_service(check), name="t")

    assert _listed(server, None) == {"t"}
    assert check.seen[0]["principal"].is_anonymous


def test_a_selector_tool_is_judged_by_its_spec_s_checks() -> None:
    server = _server()
    server.register_tool(
        SelectorSpec(kind=SelectorKind.LIST, selector=_rows, permissions=[_IsStaff()]),
        name="rows",
    )

    assert _listed(server, _user("staff", is_staff=True)) == {"rows"}
    assert _listed(server, _user("ann")) == set()


def test_a_resource_is_judged_by_its_spec_s_checks() -> None:
    server = _server()
    server.register_resource(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_one, permissions=[_IsStaff()]),
        uri_template="rows://{pk}",
        name="row",
    )
    [binding] = server.resources.templates()
    request = HttpRequest()

    assert isinstance(binding, KernelResourceBinding)
    staff = TokenInfo(user=_user("staff", is_staff=True))
    assert is_binding_listable(binding, request, staff) is True
    assert is_binding_listable(binding, request, TokenInfo(user=_user("ann"))) is False


def test_a_handbuilt_binding_whose_spec_declares_no_checks_is_hidden() -> None:
    # Its call is refused for everyone, since only the registrar's mark earns an
    # unguarded spec its grant, so it is listed to no one, and the listing still
    # answers rather than raising the kernel's configuration error.
    binding = KernelToolBinding(name="t", description=None, spec=ServiceSpec(service=_write))

    assert is_binding_listable(binding, HttpRequest(), TokenInfo(user=None)) is False


def test_a_registered_unguarded_tool_is_listed_where_its_call_is_granted() -> None:
    # The registrar marks a spec declaring no checks when the setting is off,
    # and a call to it is granted on a server with the setting off, so it lists
    # there; a server with the setting on holding the same binding grants no
    # call, and does not list it.
    config = replace(
        build_mcp_config(), filter_listings_by_permissions=True, require_tool_permissions=False
    )
    server = MCPServer(name="open", auth_backend=HeaderAuthBackend(), config=config)
    with pytest.warns(UnguardedToolWarning):
        server.register_tool(ServiceSpec(service=_write), name="t")

    assert _listed(server, None) == {"t"}

    strict = _server()
    strict.tools.register(server.tools.get("t"))
    assert _listed(strict, None) == set()


@pytest.mark.parametrize(
    ("handler", "key", "uri"),
    [
        pytest.param(handle_tools_list, "tools", None, id="tools"),
        pytest.param(handle_resources_list, "resources", "rows://all", id="resources"),
        pytest.param(
            handle_resources_templates_list, "resourceTemplates", "rows://{pk}", id="templates"
        ),
    ],
)
def test_each_spec_listing_hands_the_server_s_seeds_and_setting(
    handler: Any, key: str, uri: str | None
) -> None:
    # Each handler hands its context's pool seeds and config through, which no
    # other test tells apart from the defaults: the seed reaches the check, and
    # a marked unguarded spec is hidden by a context whose setting is on.
    check = _Recording()
    seeds = DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme")
    lenient = MCPServer(
        name="lenient",
        auth_backend=HeaderAuthBackend(),
        config=replace(build_mcp_config(), require_tool_permissions=False),
    )
    server = _server(pool_seeds=seeds)
    with pytest.warns(UnguardedToolWarning):
        if uri is None:
            server.register_tool(_service(check), name="guarded")
            server.tools.register(lenient.register_tool(ServiceSpec(service=_write), name="open"))
        else:
            spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_one, permissions=[check])
            server.register_resource(spec, uri_template=uri, name="guarded")
            open_spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_one)
            server.resources.register(
                lenient.register_resource(open_spec, uri_template=f"{uri}/open", name="open")
            )
    context = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        config=server.config,
        pool_seeds=seeds,
    )

    out = handler(None, context)

    assert isinstance(out, dict)
    assert [item["name"] for item in out[key]] == ["guarded"]
    assert [seen["tenant"] for seen in check.seen] == ["acme"]
