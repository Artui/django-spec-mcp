"""``MCPServer``'s registrars, collaborators, session namespacing and URL mounting.

The registrars are asked here with kernel specs: ``register_tool`` and the
kernel form of ``register_resource``. The rest of the drf-services original
built no spec and is carried as it asked it. Its decorator tests
(``service_tool``, ``selector_tool``, ``resource``) are not moved, because the
decorators build drf-services specs from serializers and go with them; a kernel
spec is registered through the registrars, which are the one way in. The
original is kept beside this file as ``test_mcp_server_drfs.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
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
from django_spec_mcp.auth.backends.django_oauth_toolkit_backend import (
    DjangoOAuthToolkitBackend,
)
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
    assert isinstance(server.auth_backend, DjangoOAuthToolkitBackend)
    assert isinstance(server.session_store, DjangoCacheSessionStore)


def test_default_session_store_is_namespaced_to_the_server() -> None:
    """The cache-backed store shares one Django cache across every server in
    the process, so its key space has to be per-server or two mounts collide."""
    internal = MCPServer(name="i", url_namespace="internal-mcp")
    public = MCPServer(name="p", url_namespace="public-mcp")

    internal_id = internal.session_store.create(principal_id="user:1")

    assert internal.session_store.owner(internal_id) == "user:1"
    assert public.session_store.owner(internal_id) is None


def test_sessions_are_keyed_on_name_not_url_namespace() -> None:
    """Two servers sharing a URL namespace but named differently stay isolated.

    ``url_namespace`` is a routing detail: a server used only in-process (the
    django-ag-ui bridge) is never mounted, so its namespace is a meaningless
    default and would collide with a mounted server at the default namespace —
    a collision Django's urls.W005 duplicate-namespace check cannot see,
    because an unmounted server isn't in the URL conf.
    """
    in_process = MCPServer(name="bridge")  # never mounted; url_namespace is the default
    mounted = MCPServer(name="public-api")  # also the default url_namespace

    session_id = in_process.session_store.create(principal_id="user:1")

    assert in_process.session_store.owner(session_id) == "user:1"
    assert mounted.session_store.owner(session_id) is None


def test_renaming_a_url_prefix_does_not_drop_sessions() -> None:
    """Identity, not routing, owns the key space — so a cosmetic URL refactor
    is not a silent session purge."""
    before = MCPServer(name="stable", url_namespace="old-prefix")
    after = MCPServer(name="stable", url_namespace="new-prefix")

    session_id = before.session_store.create(principal_id="user:7")

    assert after.session_store.owner(session_id) == "user:7"


def test_free_form_names_produce_usable_cache_keys() -> None:
    """``name`` is consumer-supplied prose, but cache keys must survive
    backends (memcached) that reject spaces and control characters."""
    server = MCPServer(name="My Invoicing Server ✨")
    session_id = server.session_store.create(principal_id="user:3")

    assert server.session_store.owner(session_id) == "user:3"


def test_a_hand_built_session_store_keeps_its_own_namespace() -> None:
    """Passing a store opts out of the server's namespacing — the consumer owns
    it, and two default-constructed stores collide exactly as before."""
    store = DjangoCacheSessionStore()
    server = MCPServer(name="s", url_namespace="ignored", session_store=store)
    session_id = server.session_store.create(principal_id="user:2")

    assert DjangoCacheSessionStore().owner(session_id) == "user:2"


def test_resource_url_configures_the_default_backend() -> None:
    server = MCPServer(name="internal", resource_url="https://example.com/internal/mcp/")
    md = server.auth_backend.protected_resource_metadata()
    assert md.resource == "https://example.com/internal/mcp/"


def test_resource_url_with_a_custom_backend_raises() -> None:
    """A custom backend owns its own audience binding, so resource_url= has
    nowhere to go — silently dropping it would leave enforcement unconfigured
    in a project that believes it configured it."""
    with pytest.raises(ImproperlyConfigured, match="not both"):
        MCPServer(
            name="s",
            resource_url="https://example.com/mcp/",
            auth_backend=AllowAnyBackend(),
        )


def test_urls_is_the_namespaced_triple() -> None:
    patterns, app_name, namespace = _make().urls
    # (patterns, app_name, namespace) — the shape path() mounts directly, like
    # admin.site.urls (no include()).
    assert app_name == namespace == "mcp"
    assert [p.name for p in patterns] == ["endpoint", "protected-resource-metadata"]


def test_async_urls_is_the_namespaced_triple() -> None:
    patterns, app_name, namespace = _make().async_urls
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
    # ROOT_URLCONF), so the namespaced names reverse without include().
    from django.urls import reverse

    assert reverse("mcp:endpoint") == "/mcp/"
    assert reverse("mcp:protected-resource-metadata") == "/mcp/.well-known/oauth-protected-resource"
