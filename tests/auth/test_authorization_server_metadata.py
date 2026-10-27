"""``AuthorizationServerMetadata.to_dict`` is the RFC 8414 wire shape."""

from __future__ import annotations

from django_spec_mcp.auth.types.authorization_server_metadata import (
    AuthorizationServerMetadata,
)


def test_to_dict_carries_every_field_under_its_rfc_8414_name() -> None:
    metadata = AuthorizationServerMetadata(
        issuer="https://as.example.test",
        authorization_endpoint="https://as.example.test/oauth/authorize/",
        token_endpoint="https://as.example.test/oauth/token/",
        scopes_supported=["invoices:read"],
        client_id_metadata_document_supported=True,
    )

    assert metadata.to_dict() == {
        "issuer": "https://as.example.test",
        "authorization_endpoint": "https://as.example.test/oauth/authorize/",
        "token_endpoint": "https://as.example.test/oauth/token/",
        "registration_endpoint": "",
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["invoices:read"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_basic",
            "client_secret_post",
            "none",
        ],
        "client_id_metadata_document_supported": True,
    }


def test_to_dict_hands_back_copies_not_the_frozen_instances_lists() -> None:
    metadata = AuthorizationServerMetadata(issuer="https://as.example.test")

    metadata.to_dict()["scopes_supported"].append("leaked")

    assert metadata.scopes_supported == []
