from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from django.http import HttpResponse

from django_spec_mcp.auth.insufficient_scope_response import (
    build_insufficient_scope_response,
)
from django_spec_mcp.auth.unauthenticated_response import build_unauthenticated_response


@pytest.mark.parametrize(
    ("build", "challenge", "status", "error"),
    [
        (build_unauthenticated_response, 'Bearer realm="x"', 401, "unauthorized"),
        (build_insufficient_scope_response, 'Bearer scope="x"', 403, "insufficient_scope"),
    ],
    ids=["unauthenticated", "insufficient-scope"],
)
def test_the_response_carries_the_challenge(
    build: Callable[[str], HttpResponse], challenge: str, status: int, error: str
) -> None:
    resp = build(challenge)
    assert resp.status_code == status
    assert resp["WWW-Authenticate"] == challenge
    assert json.loads(resp.content)["error"] == error
