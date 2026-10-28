"""What a kernel spec's permission check is asked on the wire.

A kernel ``PermissionCheck`` is asked about a principal and a spec, never about
a view. Two answers are asked over HTTP, on the sync and the async mount: a
RETRIEVE resource whose check refuses the row answers ``-32006``, and a check
declaring ``request`` is handed the ``HttpRequest`` the call arrived on and is
asked once.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django_service_specs import (
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
)

from django_spec_mcp import MCPServer
from tests.kernel_client import KernelClient
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.urlconf_for import urlconf_for

pytestmark = pytest.mark.django_db(transaction=True)


class _OwnRowsOnly(PermissionCheck):
    """Class-level check passes for everyone; ownership lives per row."""

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return True

    def has_object_permission(self, principal: Any, spec: Any, target: Any) -> bool:
        return bool(target["owner"] == "alice")


class _Asked(PermissionCheck):
    """Records the ``request`` each question is handed."""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    def has_permission(self, principal: Any, spec: Any, *, request: Any) -> bool:
        self.requests.append(request)
        return True


def _row(*, pk: str) -> dict[str, str]:
    return {"owner": "alice" if pk == "1" else "bob"}


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def is_async(request: pytest.FixtureRequest) -> bool:
    return bool(request.param)


def _caller(client: Client) -> KernelClient:
    caller = KernelClient(client)
    caller.initialize()
    return caller


def test_a_resource_read_refuses_a_row_the_object_check_denies(
    client: Client, is_async: bool
) -> None:
    server = MCPServer(name="t", auth_backend=HeaderAuthBackend())
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_row,
            reads=Parameters([Parameter("pk", "string", required=True)]),
            permissions=[_OwnRowsOnly()],
        ),
        name="row",
        uri_template="r://{pk}",
    )
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        caller = _caller(client)
        theirs = caller.post("resources/read", {"uri": "r://2"})
        mine = caller.post("resources/read", {"uri": "r://1"})

    assert theirs.status_code == 403
    assert theirs.json()["error"]["code"] == -32006
    assert "result" not in theirs.json()
    # The allowed row is read, so the refusal above is the check's and not the route's.
    assert json.loads(mine.json()["result"]["contents"][0]["text"]) == {"owner": "alice"}


def test_a_check_declaring_request_is_handed_the_calls_request_once(
    client: Client, is_async: bool
) -> None:
    check = _Asked()
    server = MCPServer(name="t", auth_backend=HeaderAuthBackend())
    server.register_tool(
        ServiceSpec(service=lambda: {"ok": True}, permissions=[check]), name="touch"
    )
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        caller = _caller(client)
        response = caller.post("tools/call", {"name": "touch", "arguments": {}})

    assert response.status_code == 200
    assert response.json()["result"]["structuredContent"] == {"ok": True}
    (seen,) = check.requests
    assert isinstance(seen, HttpRequest)
    assert (seen.method, seen.path) == ("POST", "/mcp/")
