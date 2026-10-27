"""The JSON-RPC error codes the MCP spec names, pinned on the wire of a kernel mount.

Asks of the kernel-twin server what the original, kept beside this file as
``test_error_codes_drfs.py``, asks of the drf-services conformance server: the
only error codes the spec dictates for a server of this shape, each with a
client behaviour attached to it. A client may special-case ``-32002`` to mean
"that resource is gone" and ``-32602`` to mean "the request itself was wrong,
don't retry it unchanged". They are asserted through the live transport, on
both views, because the number a client sees is the contract.

The denial is a kernel ``PermissionCheck`` refusing the anonymous caller, where
the original's was DRF's ``IsAuthenticated`` on a drf-services spec; the
kernel's ``NotPermitted`` is what ``KERNEL_REFUSALS`` maps to ``-32006``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from django.test import Client, override_settings
from django_service_specs import PermissionCheck, ServiceSpec

from tests.kernel_client import PROTOCOL_VERSION, KernelClient
from tests.testapp.mcp import build_kernel_server
from tests.testapp.urlconf_for import urlconf_for

pytestmark = pytest.mark.django_db(transaction=True)


class _IsAuthenticated(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:
        return bool(principal.is_authenticated)


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def mount(request: pytest.FixtureRequest) -> Iterator[None]:
    server = build_kernel_server()
    server.register_tool(
        ServiceSpec(service=lambda: {"ok": "true"}, permissions=[_IsAuthenticated()]),
        name="gated",
    )
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=request.param)):
        yield


@pytest.fixture
def caller(mount: None, client: Client) -> KernelClient:
    """An anonymous caller with a session: no ``X-Test-User`` header."""
    kernel_client = KernelClient(client)
    kernel_client.initialize()
    return kernel_client


def test_an_unknown_resource_is_32002(caller: KernelClient) -> None:
    """``-32002`` is the one legacy code the current revision keeps alive."""
    response = caller.post("resources/read", {"uri": "conformance://nothing-here"})
    assert response.status_code == 200, response.content
    error = response.json()["error"]
    assert error["code"] == -32002
    # The spec's worked example echoes the URI in ``data``.
    assert error["data"] == {"uri": "conformance://nothing-here"}


def test_an_unknown_tool_is_32602(caller: KernelClient) -> None:
    response = caller.post("tools/call", {"name": "no_such_tool", "arguments": {}})
    assert response.status_code == 200, response.content
    assert response.json()["error"]["code"] == -32602


def test_an_unknown_prompt_is_32602(caller: KernelClient) -> None:
    response = caller.post("prompts/get", {"name": "no_such_prompt", "arguments": {}})
    assert response.status_code == 200, response.content
    assert response.json()["error"]["code"] == -32602


def test_an_unknown_method_is_32601(caller: KernelClient) -> None:
    response = caller.post("nonsense/method", {})
    assert response.status_code == 200, response.content
    assert response.json()["error"]["code"] == -32601


def test_a_kernel_permission_denial_does_not_squat_on_32002(caller: KernelClient) -> None:
    """A denial must not look like a missing resource.

    ``-32002`` used to be this package's ``FORBIDDEN`` code, which meant a
    spec-following client read every permission denial as "resource not
    found". The denial carries an implementation-defined code, and the HTTP
    status is what a client acts on.
    """
    response = caller.post("tools/call", {"name": "gated", "arguments": {}})
    assert response.status_code == 403, response.content
    assert response.json()["error"] == {"code": -32006, "message": "Insufficient permission"}


def test_server_discover_needs_no_session_or_version_header(mount: None, client: Client) -> None:
    """Discovery is the first thing a client sends, so it needs no handshake."""
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "server/discover"}),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["resultType"] == "complete"
    assert result["supportedVersions"]
    assert "Mcp-Session-Id" not in response.headers


def test_a_non_sessionless_method_still_requires_a_session(mount: None, client: Client) -> None:
    """**400, not 404**: a request carrying no session header "SHOULD" get 400."""
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": PROTOCOL_VERSION},
    )
    assert response.status_code == 400, response.content


def test_an_unknown_session_id_is_404_not_400(mount: None, client: Client) -> None:
    """An id the server does not honour is a 404, which tells a client to re-``initialize``."""
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": PROTOCOL_VERSION, "Mcp-Session-Id": "no-such-session"},
    )
    assert response.status_code == 404, response.content
