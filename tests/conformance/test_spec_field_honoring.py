from __future__ import annotations

import pytest

pytestmark = pytest.mark.urls("tests.conformance.urls")


@pytest.mark.django_db(transaction=True)
def test_spec_permissions_deny_anonymous_user(jsonrpc, initialized_session: str) -> None:
    response = jsonrpc(
        "tools/call",
        {"name": "conformance.gated", "arguments": {}},
        session_id=initialized_session,
    )
    # The spec's ``IsAuthenticated`` check refuses the AllowAnyBackend-issued
    assert response.status_code == 403, response.content
    body = response.json()
    assert "error" in body
    assert body["error"]["code"] == -32006
    assert "scope=" not in response["WWW-Authenticate"]
