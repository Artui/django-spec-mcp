"""``bearer_challenge`` builds the ``WWW-Authenticate`` value a backend answers 401 with.

The format is fixed here once so no project's backend writes it again. The
parameter order is part of it: the realm, then the metadata pointer, then the
error, then the scope, as a client parsing the header reads them.
"""

from __future__ import annotations

from django_spec_mcp.auth import bearer_challenge
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend

_PRM = "https://example.test/mcp/.well-known/oauth-protected-resource"


def test_a_bare_challenge_is_the_realm_alone() -> None:
    assert bearer_challenge(resource_metadata_url=None) == 'Bearer realm="mcp"'


def test_the_metadata_url_is_the_pointer_a_client_finds_the_authorization_server_by() -> None:
    """Without ``resource_metadata`` a client has no way to discover the authorization server."""
    assert (
        bearer_challenge(resource_metadata_url=_PRM)
        == f'Bearer realm="mcp", resource_metadata="{_PRM}"'
    )


def test_an_empty_metadata_url_is_left_out() -> None:
    assert bearer_challenge(resource_metadata_url="") == 'Bearer realm="mcp"'


def test_scopes_are_joined_by_a_space_in_one_parameter() -> None:
    assert (
        bearer_challenge(resource_metadata_url=None, scopes=["orders:read", "orders:write"])
        == 'Bearer realm="mcp", scope="orders:read orders:write"'
    )


def test_an_empty_scope_list_is_left_out() -> None:
    assert bearer_challenge(resource_metadata_url=None, scopes=[]) == 'Bearer realm="mcp"'


def test_the_error_is_quoted() -> None:
    assert (
        bearer_challenge(resource_metadata_url=None, error="invalid_token")
        == 'Bearer realm="mcp", error="invalid_token"'
    )


def test_every_part_appears_in_the_order_a_client_reads_them() -> None:
    assert bearer_challenge(
        resource_metadata_url=_PRM, scopes=["a", "b"], error="insufficient_scope"
    ) == (
        f'Bearer realm="mcp", resource_metadata="{_PRM}", error="insufficient_scope", scope="a b"'
    )


def test_the_development_backend_answers_with_the_helper() -> None:
    backend = AllowAnyBackend()

    assert backend.www_authenticate_challenge() == bearer_challenge(resource_metadata_url=None)
    assert backend.www_authenticate_challenge(
        scopes=["a"], error="invalid_token"
    ) == bearer_challenge(resource_metadata_url=None, scopes=["a"], error="invalid_token")
