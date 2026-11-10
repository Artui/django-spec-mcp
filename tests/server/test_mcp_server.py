from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django_service_specs import (
    DataclassPresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.server.mcp_server import MCPServer
from django_spec_mcp.transport.django_cache_session_store import DjangoCacheSessionStore
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.services import record_invoice


def _make() -> MCPServer:
    """Build a server that doesn't depend on Django settings for collaborators."""
    return MCPServer(
        name="test",
        description="d",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
    )


@dataclass
class _Note:
    pk: int


def _service() -> ServiceSpec:
    return ServiceSpec(service=record_invoice, permissions=[Unrestricted()])


def _retrieve() -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda *, pk: _Note(pk=pk),
        reads=Parameters([Parameter("pk", "integer", required=True)]),
        permissions=[Unrestricted()],
        presenter=DataclassPresenter(_Note),
    )


def test_register_tool_imperative() -> None:
    server = _make()
    binding = server.register_tool(_service(), name="t", description="d")
    assert server.tools.get("t") is binding


def test_register_resource_imperative() -> None:
    server = _make()
    binding = server.register_resource(_retrieve(), name="r", uri_template="r://{pk}")
    assert server.resources.resolve("r://1") is not None
    assert binding.name == "r"


def test_register_resource_rejects_bare_callable() -> None:
    """The kernel form requires a ``SelectorSpec``."""
    server = _make()
    bare: Any = lambda: None  # noqa: E731
    with pytest.raises(TypeError, match="SelectorSpec"):
        server.register_resource(bare, name="r", uri_template="r://")


def test_register_tool_duplicate_raises() -> None:
    server = _make()
    server.register_tool(_service(), name="dup", description="d")
    with pytest.raises(ValueError, match="Duplicate"):
        server.register_tool(_service(), name="dup", description="d")


def test_accessors() -> None:
    server = _make()
    assert server.auth_backend is not None
    assert server.session_store is not None


def test_omitted_collaborators_get_constructed_defaults() -> None:
    """No dotted-path resolution: the package defaults are built directly."""
    server = MCPServer(name="s")
    assert server.auth_backend is None
    assert isinstance(server.session_store, DjangoCacheSessionStore)


def test_default_session_store_is_namespaced_to_the_server() -> None:
    """The cache-backed store shares one Django cache across every server in the process."""
    internal = MCPServer(name="i", url_namespace="internal-mcp")
    public = MCPServer(name="p", url_namespace="public-mcp")

    internal_id = internal.session_store.create(principal_id="user:1")

    assert internal.session_store.owner(internal_id) == "user:1"
    assert public.session_store.owner(internal_id) is None


def test_sessions_are_keyed_on_name_not_url_namespace() -> None:
    """Two servers sharing a URL namespace but named differently stay isolated."""
    in_process = MCPServer(name="bridge")
    mounted = MCPServer(name="public-api")

    session_id = in_process.session_store.create(principal_id="user:1")

    assert in_process.session_store.owner(session_id) == "user:1"
    assert mounted.session_store.owner(session_id) is None


def test_renaming_a_url_prefix_does_not_drop_sessions() -> None:
    """Identity, not routing, owns the key space, so a URL refactor purges nothing."""
    before = MCPServer(name="stable", url_namespace="old-prefix")
    after = MCPServer(name="stable", url_namespace="new-prefix")

    session_id = before.session_store.create(principal_id="user:7")

    assert after.session_store.owner(session_id) == "user:7"


def test_free_form_names_produce_usable_cache_keys() -> None:
    """``name`` is consumer-supplied prose, but cache keys must survive backends."""
    server = MCPServer(name="My Invoicing Server ✨")
    session_id = server.session_store.create(principal_id="user:3")

    assert server.session_store.owner(session_id) == "user:3"


def test_a_hand_built_session_store_keeps_its_own_namespace() -> None:
    """Passing a store opts out of the server's namespacing."""
    store = DjangoCacheSessionStore()
    server = MCPServer(name="s", url_namespace="ignored", session_store=store)
    session_id = server.session_store.create(principal_id="user:2")

    assert DjangoCacheSessionStore().owner(session_id) == "user:2"


@pytest.mark.parametrize("attribute", ["urls", "async_urls"])
def test_urls_is_the_namespaced_triple(attribute: str) -> None:
    patterns, app_name, namespace = getattr(_make(), attribute)
    # (patterns, app_name, namespace) — the shape path() mounts directly, like
    assert app_name == namespace == "mcp"
    assert [p.name for p in patterns] == ["endpoint", "protected-resource-metadata"]


def test_url_namespace_is_overridable() -> None:
    server = MCPServer(
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        url_namespace="tools",
    )
    _, app_name, namespace = server.urls
    assert app_name == namespace == "tools"


def test_urls_mount_via_path_and_reverse_namespaced() -> None:
    # tests.testapp.urls mounts path("mcp/", server.urls) (the conftest
    from django.urls import reverse

    assert reverse("mcp:endpoint") == "/mcp/"
    assert reverse("mcp:protected-resource-metadata") == "/mcp/.well-known/oauth-protected-resource"
