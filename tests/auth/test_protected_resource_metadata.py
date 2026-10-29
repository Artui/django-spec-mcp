"""The RFC 9728 document a backend publishes, as it goes on the wire."""

from __future__ import annotations

from django_spec_mcp.auth.types.protected_resource_metadata import ProtectedResourceMetadata


def test_the_document_carries_the_rfc_fields_and_leaves_out_what_is_unset() -> None:
    metadata = ProtectedResourceMetadata(
        resource="https://example.test/mcp/",
        authorization_servers=["https://idp.example.test"],
        scopes_supported=["orders:read"],
    )

    assert metadata.to_dict() == {
        "resource": "https://example.test/mcp/",
        "authorization_servers": ["https://idp.example.test"],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["orders:read"],
    }


def test_a_documentation_link_is_published_as_resource_documentation() -> None:
    metadata = ProtectedResourceMetadata(
        resource="https://example.test/mcp/", resource_documentation="https://example.test/docs"
    )

    assert metadata.to_dict()["resource_documentation"] == "https://example.test/docs"


def test_a_warning_is_published_under_the_underscore_extension() -> None:
    metadata = ProtectedResourceMetadata(resource="(unset)", warning="development only")

    assert metadata.to_dict()["_warning"] == "development only"
