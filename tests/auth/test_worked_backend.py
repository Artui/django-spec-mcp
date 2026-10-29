"""The backend the authentication page walks through, run as written.

The page's class is read out of the page and executed here, so the worked
example cannot drift from the seam it teaches: ``bearer_challenge``,
``audience_matches`` and the RFC 9728 metadata document.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import reverse

from django_spec_mcp.auth import TokenInfo
from django_spec_mcp.auth.types.auth_backend import MCPAuthBackend

_PAGE = Path(__file__).resolve().parents[2] / "docs" / "auth.md"
_RESOURCE = "https://example.test/mcp/"


def _worked_backend_class() -> type:
    """Execute the page's fence that defines ``BearerBackend``."""
    fences = _PAGE.read_text().split("```python\n")[1:]
    (source,) = [f.split("```")[0] for f in fences if "class BearerBackend" in f]
    namespace: dict[str, Any] = {}
    exec(compile(source, str(_PAGE), "exec"), namespace)
    return namespace["BearerBackend"]


def _backend(audience: str | None) -> Any:
    info = TokenInfo(user=AnonymousUser(), scopes=("orders:read",), audience=audience)
    return _worked_backend_class()(
        resource_url=_RESOURCE,
        authorization_server="https://idp.example.test",
        scopes_supported=["orders:read"],
        verify=lambda raw: info if raw == "good" else None,
    )


def _request(authorization: str | None) -> Any:
    extra = {} if authorization is None else {"HTTP_AUTHORIZATION": authorization}
    return RequestFactory().get("/mcp/", **extra)


def test_the_worked_backend_satisfies_the_protocol() -> None:
    assert isinstance(_backend(_RESOURCE), MCPAuthBackend)


def test_a_token_bound_to_this_server_authenticates() -> None:
    info = _backend(_RESOURCE).authenticate(_request("Bearer good"))

    assert info is not None
    assert info.audience == _RESOURCE
    assert info.scopes == ("orders:read",)


def test_a_token_bound_to_another_resource_is_refused() -> None:
    """It verifies, and the audience comparison is what still turns it away."""
    backend = _backend("https://example.test/mcp-admin/")

    assert backend.authenticate(_request("Bearer good")) is None


def test_a_token_bound_to_no_resource_is_refused() -> None:
    assert _backend(None).authenticate(_request("Bearer good")) is None


@pytest.mark.parametrize("header", [None, "", "Bearer", "Basic good", "Bearer bad"])
def test_a_request_without_a_verifying_bearer_token_is_refused(header: str | None) -> None:
    assert _backend(_RESOURCE).authenticate(_request(header)) is None


def test_the_challenge_points_at_the_route_the_server_mounts_its_metadata_on() -> None:
    """Derived from the resource URL, and equal to what ``reverse`` answers at the mount."""
    pointer = "https://example.test" + reverse("mcp:protected-resource-metadata")

    assert (
        _backend(_RESOURCE).www_authenticate_challenge(
            scopes=["orders:read"], error="invalid_token"
        )
        == f'Bearer realm="mcp", resource_metadata="{pointer}", '
        'error="invalid_token", scope="orders:read"'
    )


def test_the_metadata_names_the_resource_and_its_authorization_server() -> None:
    assert _backend(_RESOURCE).protected_resource_metadata().to_dict() == {
        "resource": _RESOURCE,
        "authorization_servers": ["https://idp.example.test"],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["orders:read"],
    }
