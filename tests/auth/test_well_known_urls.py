"""The metadata document at RFC 9728's location, beside the one under the mount.

RFC 9728 3.1 places a resource's metadata at the origin, with the resource's
path after the well-known prefix, and the MCP authorization spec makes that the
place a client looks when a 401 carries no ``resource_metadata``. Everything
``server.urls`` mounts sits under the mount's own prefix, so that location is
reachable only from the root URLConf, which ``well_known_urls()`` is added to.
"""

from __future__ import annotations

import re
import types

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, override_settings
from django.urls import URLPattern, path

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.protected_resource_metadata_view import ProtectedResourceMetadataView
from django_spec_mcp.auth.types.protected_resource_metadata import ProtectedResourceMetadata


class _ResourceBackend(AllowAnyBackend):
    """Publishes ``resource`` as the protected resource's identifier."""

    def __init__(self, resource: str) -> None:
        self._resource = resource

    def protected_resource_metadata(self) -> ProtectedResourceMetadata:
        return ProtectedResourceMetadata(
            resource=self._resource, authorization_servers=["https://idp.example.test"]
        )


def _server(resource: str, *, name: str = "orders") -> MCPServer:
    return MCPServer(name=name, auth_backend=_ResourceBackend(resource), url_namespace=name)


def _root_urlconf(*mounts: tuple[str, MCPServer]) -> types.ModuleType:
    """A root URLConf as the Authentication page writes it."""
    module = types.ModuleType("tests.auth._well_known_urlconf")
    patterns: list[URLPattern] = []
    for prefix, server in mounts:
        patterns += [path(prefix, server.urls), *server.well_known_urls()]
    module.urlpatterns = patterns  # ty: ignore[unresolved-attribute]
    return module


def test_the_document_answers_at_rfc_9728s_location_as_it_does_under_the_mount() -> None:
    """The done-condition the consumer carries: the same document at both URLs."""
    server = _server("https://example.test/mcp/")

    with override_settings(ROOT_URLCONF=_root_urlconf(("mcp/", server))):
        at_origin = Client().get("/.well-known/oauth-protected-resource/mcp/")
        under_mount = Client().get("/mcp/.well-known/oauth-protected-resource")

    assert at_origin.status_code == 200
    assert at_origin.json() == under_mount.json()
    assert at_origin.json()["resource"] == "https://example.test/mcp/"


@pytest.mark.parametrize(
    ("resource", "location"),
    [
        ("https://example.test/mcp/", "/.well-known/oauth-protected-resource/mcp/"),
        ("https://example.test/mcp", "/.well-known/oauth-protected-resource/mcp"),
        (
            "https://example.test/internal/mcp/",
            "/.well-known/oauth-protected-resource/internal/mcp/",
        ),
        ("https://example.test", "/.well-known/oauth-protected-resource"),
        ("https://example.test/", "/.well-known/oauth-protected-resource"),
        ("http://localhost:8000/mcp/", "/.well-known/oauth-protected-resource/mcp/"),
        ("https://example.test/mcp/?tenant=a", "/.well-known/oauth-protected-resource/mcp/"),
    ],
    ids=["slash", "no-slash", "nested", "origin", "origin-slash", "http", "query"],
)
def test_the_location_is_built_from_the_resource_the_document_names(
    resource: str, location: str
) -> None:
    """RFC 9728 3.1: the well-known path goes between the host and the resource's path.

    A terminating slash right after the host is dropped, and a query stays a
    query, which Django's resolver does not match on. Built from the
    document's own ``resource``, the location agrees with what the client
    checks the document against (RFC 9728 3.3).
    """
    server = _server(resource)

    with override_settings(ROOT_URLCONF=_root_urlconf(("mcp/", server))):
        response = Client().get(location)

    assert response.status_code == 200
    assert response.json()["resource"] == resource


@pytest.mark.parametrize(
    ("resource", "stripped"),
    [
        ("https://example.test/mcp/", "/.well-known/oauth-protected-resource/mcp"),
        (
            "https://example.test/internal/mcp/",
            "/.well-known/oauth-protected-resource/internal/mcp",
        ),
        (
            "https://example.test/caf%C3%A9/mcp/",
            "/.well-known/oauth-protected-resource/caf%C3%A9/mcp",
        ),
    ],
    ids=["slash", "nested", "encoded"],
)
@override_settings(MIDDLEWARE=[])
def test_a_resource_with_a_trailing_slash_also_answers_without_it(
    resource: str, stripped: str
) -> None:
    """The TypeScript SDK strips the resource's trailing slash when it builds the URL.

    ``@modelcontextprotocol/sdk`` 1.29.0 asks ``/.well-known/oauth-protected-resource/mcp``
    for ``https://example.com/mcp/``. With no ``CommonMiddleware`` there is no
    ``APPEND_SLASH`` redirect to forgive it, so the stripped route must answer
    itself, with the document the slashed route answers.
    """
    server = _server(resource)

    with override_settings(ROOT_URLCONF=_root_urlconf(("mcp/", server))):
        response = Client().get(stripped)
        under_mount = Client().get("/mcp/.well-known/oauth-protected-resource")

    assert response.status_code == 200
    assert response.json() == under_mount.json()
    assert response.json()["resource"] == resource


@pytest.mark.parametrize(
    ("resource", "routes"),
    [
        ("https://example.test/mcp/", 2),
        ("https://example.test/mcp", 1),
        ("https://example.test/", 1),
        ("https://example.test", 1),
    ],
    ids=["slash", "no-slash", "origin-slash", "origin"],
)
def test_the_stripped_route_is_added_only_for_a_path_that_ends_in_a_slash(
    resource: str, routes: int
) -> None:
    """A resource without a trailing slash, or the origin alone, has no second form."""
    assert len(_server(resource).well_known_urls()) == routes


@pytest.mark.parametrize(
    ("resource", "location"),
    [
        (
            "https://example.test/caf%C3%A9/mcp/",
            "/.well-known/oauth-protected-resource/caf%C3%A9/mcp/",
        ),
        ("https://example.test/m%20cp/", "/.well-known/oauth-protected-resource/m%20cp/"),
        ("https://example.test/m%20cp", "/.well-known/oauth-protected-resource/m%20cp"),
        ("https://example.test/a%2Fb/mcp/", "/.well-known/oauth-protected-resource/a%2Fb/mcp/"),
    ],
    ids=["utf8", "space", "space-no-slash", "encoded-slash"],
)
def test_a_percent_encoded_resource_path_answers_at_its_own_location(
    resource: str, location: str
) -> None:
    """Django resolves the decoded ``path_info``, so the route is built from the decoded path.

    A client asks for the encoded URL (RFC 9728 3.1 appends the resource's path
    as it is written), and the resolver sees ``/café/mcp/``.
    """
    server = _server(resource)

    with override_settings(ROOT_URLCONF=_root_urlconf(("mcp/", server))):
        response = Client().get(location)

    assert response.status_code == 200
    assert response.json()["resource"] == resource


@pytest.mark.parametrize(
    "resource",
    [
        "https://example.test/<int:x>/mcp/",
        "https://example.test/%3Cint:x%3E/mcp/",
        "https://example.test/mcp>/",
        "https://example.test/mcp%3e/",
        "https://example.test/<mcp/",
        "https://example.test/%3Cmcp/",
    ],
    ids=[
        "raw",
        "encoded",
        "closing-only",
        "encoded-lower-case",
        "opening-only",
        "encoded-opening-only",
    ],
)
def test_a_resource_path_with_angle_brackets_is_refused_by_name(resource: str) -> None:
    """Not legal in a URI, and Django would read ``<int:x>`` as a path converter.

    Refused whether written raw or percent-encoded, since the route is built
    from the decoded path and the encoded form would become a converter there.
    """
    with pytest.raises(ImproperlyConfigured, match=re.escape(f"resource {resource!r}")):
        _server(resource).well_known_urls()


@pytest.mark.parametrize(
    ("resource", "route"),
    [
        ("https://example.test/mcp/", ".well-known/oauth-protected-resource/mcp/"),
        ("https://example.test/", ".well-known/oauth-protected-resource"),
        ("https://example.test/caf%C3%A9/mcp/", ".well-known/oauth-protected-resource/café/mcp/"),
        ("https://example.test/a%2Fb/mcp/", ".well-known/oauth-protected-resource/a/b/mcp/"),
    ],
    ids=["slash", "origin", "utf8", "encoded-slash"],
)
def test_the_route_is_built_from_the_decoded_path(resource: str, route: str) -> None:
    """Pins the route string, ``%2F`` included.

    The resolver matches Django's decoded ``path_info``, in which ``%2F`` is
    already ``/``, so ``a%2Fb`` is routed as the two segments ``a/b``. The
    decoding is deliberately not selective: a front server that rejects or
    keeps ``%2F`` encoded makes such a resource path unreachable.
    """
    assert ProtectedResourceMetadataView.well_known_route(resource) == route


@pytest.mark.parametrize(
    "resource",
    [
        "https://example.test/mcp$/",
        "https://example.test/%24/mcp/",
        "https://example.test/a%24b/mcp/",
        "https://example.test/mcp%24",
        "https://example.test/$",
    ],
    ids=["raw-trailing", "encoded-leading", "encoded-inner", "encoded-last", "raw-only"],
)
def test_a_resource_path_with_a_dollar_sign_is_refused_by_name(resource: str) -> None:
    """Django's route check reads a route ending in ``$`` as a leftover regex anchor.

    ``path()`` matches ``$`` literally, but ``manage.py check`` warns
    ``2_0.W001`` for a route that ends with one, so the resource is refused
    where the route is built. Refused whether written raw or percent-encoded,
    since the route is built from the decoded path: the encoded cases fail if
    the check ran before the decoding. The match includes the message's own
    wording, so the test pins which refusal answered: the resources hold nothing
    the angle-bracket or UTF-8 refusals read.
    """
    named = re.escape(f"resource {resource!r}")
    with pytest.raises(ImproperlyConfigured, match=named + r".*holds '\$'"):
        _server(resource).well_known_urls()


@pytest.mark.parametrize(
    "resource",
    [
        "https://example.test/%FF/mcp/",
        "https://example.test/mcp%C3/",
        "https://example.test/m%E2%82",
    ],
    ids=["lone-continuation", "truncated-two-byte", "truncated-three-byte"],
)
def test_a_resource_path_that_does_not_decode_as_utf8_is_refused_by_name(resource: str) -> None:
    """A lenient decode would put U+FFFD in the route, which no request matches.

    The location would be built and 404 silently, so the decoding is strict and
    a path it cannot read is refused with the resource named.
    """
    with pytest.raises(ImproperlyConfigured, match=re.escape(f"resource {resource!r}")):
        _server(resource).well_known_urls()


def test_two_servers_each_answer_at_their_own_location() -> None:
    internal = _server("https://example.test/internal/mcp/", name="internal-mcp")
    public = _server("https://example.test/public/mcp/", name="public-mcp")

    with override_settings(
        ROOT_URLCONF=_root_urlconf(("internal/mcp/", internal), ("public/mcp/", public))
    ):
        internal_doc = Client().get("/.well-known/oauth-protected-resource/internal/mcp/")
        public_doc = Client().get("/.well-known/oauth-protected-resource/public/mcp/")

    assert internal_doc.json()["resource"] == "https://example.test/internal/mcp/"
    assert public_doc.json()["resource"] == "https://example.test/public/mcp/"


@pytest.mark.parametrize("slashed_first", [True, False], ids=["slashed-first", "bare-first"])
def test_servers_whose_resources_differ_by_a_trailing_slash_share_the_stripped_route(
    slashed_first: bool,
) -> None:
    """The first server mounted answers the route both share, in either mount order.

    ``https://example.test/mcp/`` and ``https://example.test/mcp`` both own
    ``/.well-known/oauth-protected-resource/mcp``, and the resolver takes the
    first match, so the second server's client reads the first server's
    document and must discard it (RFC 9728 3.3: its ``resource`` is not the
    identifier the URL was built from). The slashed route belongs to the slashed
    server alone, wherever it is mounted. Pinned so the shared route is known
    behaviour; the docs tell a project to give two servers distinct paths.
    """
    slashed = _server("https://example.test/mcp/", name="slashed")
    bare = _server("https://example.test/mcp", name="bare")
    mounts = [("slashed/", slashed), ("bare/", bare)]
    first = "https://example.test/mcp/" if slashed_first else "https://example.test/mcp"

    with override_settings(
        ROOT_URLCONF=_root_urlconf(*(mounts if slashed_first else mounts[::-1]))
    ):
        shared = Client().get("/.well-known/oauth-protected-resource/mcp")
        slashed_only = Client().get("/.well-known/oauth-protected-resource/mcp/")

    assert shared.json()["resource"] == first
    assert slashed_only.json()["resource"] == "https://example.test/mcp/"


def test_the_root_route_is_the_metadata_view_with_its_refusals() -> None:
    """The same view as under the mount: JSON only, ``405`` for anything but a read."""
    server = _server("https://example.test/mcp/")

    with override_settings(ROOT_URLCONF=_root_urlconf(("mcp/", server))):
        refused = Client().get(
            "/.well-known/oauth-protected-resource/mcp/", headers={"Accept": "text/html"}
        )
        posted = Client().post("/.well-known/oauth-protected-resource/mcp/")

    assert refused.status_code == 406
    assert posted.status_code == 405


def test_a_server_with_no_backend_has_no_document_to_serve() -> None:
    with pytest.raises(ImproperlyConfigured, match="no auth_backend="):
        MCPServer(name="orders").well_known_urls()


@pytest.mark.parametrize(
    "resource",
    [
        "(unset)",
        "/mcp/",
        "//example.test/mcp/",
        "ftp://example.test/mcp/",
        "https:/mcp/",
        "https://example.test/mcp/#section",
    ],
    ids=["allow-any", "relative", "no-scheme", "other-scheme", "no-host", "fragment"],
)
def test_a_resource_that_is_not_an_absolute_url_is_refused_by_name(resource: str) -> None:
    """RFC 9728 1.2 makes the identifier an absolute URL with no fragment.

    Each case drops one condition of the check: no scheme and no host, no
    scheme, a scheme but not http(s), http(s) with no host, and a fragment.
    """
    with pytest.raises(ImproperlyConfigured, match=re.escape(f"resource {resource!r}")):
        _server(resource).well_known_urls()


def test_allow_any_backend_publishes_no_resource_to_place_a_document_at() -> None:
    with pytest.raises(ImproperlyConfigured, match=re.escape("'(unset)'")):
        MCPServer(name="dev", auth_backend=AllowAnyBackend()).well_known_urls()


class _FailingSelfCheck(_ResourceBackend):
    """A backend that says it cannot run, and whose metadata would fail differently."""

    def check_configuration(self) -> None:
        raise ImproperlyConfigured("the backend cannot run")

    def protected_resource_metadata(self) -> ProtectedResourceMetadata:
        raise RuntimeError("the metadata was read before the backend was checked")


def test_a_self_checking_backend_is_asked_before_its_metadata_is_read() -> None:
    """A root URLConf may list the well-known pattern before the mount.

    Then this is the first read of the server, and the backend's own refusal is
    what must surface, not whatever reading its metadata unchecked raises.
    """
    server = MCPServer(name="orders", auth_backend=_FailingSelfCheck("https://example.test/mcp/"))

    with pytest.raises(ImproperlyConfigured, match="the backend cannot run"):
        server.well_known_urls()
