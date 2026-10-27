"""Guards on how permissions are constructed and registered, for kernel tools.

Every case here is a configuration that used to be accepted and then failed
*silently*: a binding that reads as guarded at the registration site and gates
nothing at dispatch. None of them raised, and none of them showed up in the
unguarded-tool warning, which is what made them worth refusing at the point
they are written.

On the kernel a tool has two gates, and each is asked here where it is
written. ``scopes=`` on ``register_tool`` is the transport's scope gate, and a
bare string there is one scope over the wire as in ``ScopeRequired``. The
spec's own ``permissions=`` is the kernel's, and the kernel refuses a string,
an entry that is not a ``PermissionCheck`` and an empty list when the spec is
built, so this server never holds one. ``ScopeRequired``, ``DjangoPermRequired``
and the ``permissions=`` of a prompt or a resource are still this package's and
are asked as before.

The drf-services original asked the tool half of this of
``register_service_tool(permissions=...)``, which a kernel tool does not take.
It is kept beside this file as ``test_permission_argument_guards_drfs.py``. How
``scopes=`` is stored is the registration suite's question
(``test_a_bare_string_scope_is_one_scope``, ``test_an_empty_scope_tuple_is_refused``);
this file asks what it gates.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.test import Client, override_settings
from django_service_specs import ServiceSpec, Unrestricted

from django_spec_mcp import DjangoPermRequired, MCPServer, ScopeRequired
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.utils import check_permissions
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.kernel_client import KernelClient
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.urlconf_for import urlconf_for


def _server() -> MCPServer:
    return MCPServer(name="g", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


# ----- a single scope as a bare string -----


def test_a_bare_string_scope_is_one_scope_not_nine() -> None:
    """The reported bug: ``list("mcp:admin")`` used to split into characters.

    Nothing failed at registration; it surfaced much later as a permission
    that could never be satisfied and a challenge reading
    ``scope="m c p : a d m i n"``.
    """
    assert ScopeRequired("mcp:admin").required_scopes() == ["mcp:admin"]


def test_the_list_form_is_unchanged() -> None:
    assert ScopeRequired(["a", "b"]).required_scopes() == ["a", "b"]


def test_the_two_permission_classes_now_agree_on_a_bare_string() -> None:
    """The asymmetry is what caused the bug: one sibling accepted a string,
    the other silently mangled it, and a developer who learned the first
    naturally wrote the same thing for the second."""
    assert ScopeRequired("x").required_scopes() == ["x"]
    assert DjangoPermRequired("app.perm")._perms == ["app.perm"]


def test_a_bare_string_scope_actually_gates() -> None:
    """Not just stored correctly: satisfiable by the token that should satisfy it."""
    permission = ScopeRequired("mcp:admin")
    assert permission.has_permission(None, TokenInfo(user=None, scopes=("mcp:admin",)))
    assert not permission.has_permission(None, TokenInfo(user=None, scopes=("other",)))


@pytest.mark.django_db
@pytest.mark.parametrize(("scopes", "admitted"), [("mcp:admin", True), ("other", False)])
def test_a_kernel_tools_bare_string_scope_gates_over_the_wire(
    client: Client, scopes: str, admitted: bool
) -> None:
    """``register_tool(scopes="mcp:admin")`` admits the token carrying that one
    scope, and refuses another with a challenge naming it whole."""
    server = MCPServer(
        name="g", auth_backend=HeaderAuthBackend(), session_store=InMemorySessionStore()
    )
    server.register_tool(
        ServiceSpec(service=lambda: {"ok": True}, permissions=[Unrestricted()]),
        name="t",
        scopes="mcp:admin",
    )
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        caller = KernelClient(client, scopes=scopes)
        caller.initialize()
        response = caller.post("tools/call", {"name": "t", "arguments": {}})
    if admitted:
        assert response.json()["result"]["structuredContent"] == {"ok": True}
        return
    assert response.status_code == 403
    assert response.json()["error"]["data"] == {"requiredScopes": ["mcp:admin"]}
    assert 'scope="mcp:admin"' in response["WWW-Authenticate"]


# ----- an empty permission is not a permission -----


@pytest.mark.parametrize("factory", [ScopeRequired, DjangoPermRequired])
def test_an_empty_requirement_is_refused(factory: Any) -> None:
    """``all(...)`` over nothing is ``True``, so an empty one permits everything
    while reading as a guard, and satisfies the unguarded-tool check that
    would otherwise have warned."""
    with pytest.raises(ImproperlyConfigured, match="at least one"):
        factory([])


# ----- permissions= must contain permissions -----


@pytest.mark.parametrize(
    ("permissions", "match"),
    [
        ("ScopeRequired", "must be a sequence of PermissionCheck"),
        ([object()], "is not a PermissionCheck"),
        ([], "would let anyone run it"),
    ],
    ids=["a-string", "not-a-check", "empty"],
)
def test_a_kernel_spec_refuses_permissions_that_cannot_gate(permissions: Any, match: str) -> None:
    """The kernel's answer to the original's three registration cases.

    A string would spread into one entry per character, an entry that is not
    a check gates nothing, and an empty list admits every caller; each reads
    as guarded. The kernel refuses all three when the spec is built, so
    ``register_tool`` is never handed one.
    """
    with pytest.raises(ImproperlyConfigured, match=match):
        ServiceSpec(service=lambda: {}, permissions=permissions)


def test_the_guard_covers_resources_and_prompts_too() -> None:
    """The ``permissions=`` a prompt takes is this package's, so is the check."""
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="cannot answer"):
        server.register_prompt(name="p", render=lambda: "hi", permissions=[object()])


def test_a_string_passed_to_a_prompts_permissions_is_refused() -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="one entry per character"):
        server.register_prompt(name="p", render=lambda: "hi", permissions="ScopeRequired")  # type: ignore[arg-type]


def test_a_permission_implementing_only_the_gate_is_accepted() -> None:
    """``required_scopes`` is documented as having an implied ``[]`` default.

    An ``isinstance`` check against the runtime-checkable Protocol would
    reject this, since it tests for *every* member, so the guard asks only
    for the method that actually gates.
    """

    class _GateOnly:
        def has_permission(self, request: Any, token: Any) -> bool:
            return False

    binding = _server().register_prompt(name="p", render=lambda: "hi", permissions=[_GateOnly()])
    assert len(binding.permissions) == 1


def test_a_gate_only_permission_denies_at_dispatch_as_well_as_in_listings() -> None:
    """The second bug this closes.

    ``is_binding_listable`` duck-types and honoured such a permission, while
    ``check_permissions`` skipped anything failing ``isinstance(...)`` against
    the runtime-checkable Protocol, which includes ``required_scopes``. The
    binding vanished from listings *and the call went through*.
    """

    class _GateOnly:
        def has_permission(self, request: Any, token: Any) -> bool:
            return False

    allowed, scopes = check_permissions((_GateOnly(),), HttpRequest(), TokenInfo(user=None))
    assert allowed is False
    assert scopes == []
