"""End-to-end unknown-argument policy of a kernel tool, through the live transport.

Verifies the wire shape of ``REJECT`` for a kernel spec registered with
``register_tool``: an ``isError`` ``validation_error`` result whose detail names
the unknown key, served as a ``tools/call`` result rather than a JSON-RPC
error. ``PASSTHROUGH`` has no wire shape: ``register_tool`` refuses it, since
the kernel never hands an undeclared argument to a callable, so a server
carrying it never starts.

The drf-services original asked the same of the conformance server's
drf-services selector tool, whose refusal sits under ``non_field_errors``; the
kernel's names the key instead. It is kept beside this file as
``test_unknown_arguments_matrix_drfs.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.urls import path
from django_service_specs import (
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    UnknownArguments,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from tests.utils import tool_error

pytestmark = pytest.mark.urls(__name__)


def _project_scoped(*, project_id: str) -> list[dict[str, Any]]:
    return [{"project_id": project_id}]


def _spec() -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_project_scoped,
        permissions=[Unrestricted()],
        reads=Parameters([Parameter("project_id", "string", required=True)]),
    )


def __getattr__(name: str) -> Any:
    # A server per URLconf read, so no session outlives its test: pytest-django
    # clears the resolver cache around every test (see ``tests/conformance/urls.py``).
    if name != "urlpatterns":
        raise AttributeError(name)
    server = MCPServer(name="conformance", auth_backend=AllowAnyBackend())
    server.register_tool(_spec(), name="conformance.reject_unknown")
    return [path("mcp/", server.urls)]


def _call(jsonrpc: Any, session: str, arguments: dict[str, Any]) -> dict[str, Any]:
    response = jsonrpc(
        "tools/call",
        {"name": "conformance.reject_unknown", "arguments": arguments},
        session_id=session,
    )
    assert response.status_code == 200, response.content
    return response.json()


@pytest.mark.django_db(transaction=True)
def test_reject_unknown_key_returns_a_validation_error_result(
    jsonrpc: Any, initialized_session: str
) -> None:
    # Input validation, so a tool execution error the model can read, not ``-32602``.
    body = _call(jsonrpc, initialized_session, {"project_id": "p1", "rogue": "x"})

    assert "error" not in body, body
    error = tool_error(body["result"])
    assert error["type"] == "validation_error"
    assert error["detail"] == {"rogue": ["Unknown argument."]}


@pytest.mark.django_db(transaction=True)
def test_reject_unknown_accepts_known_only(jsonrpc: Any, initialized_session: str) -> None:
    body = _call(jsonrpc, initialized_session, {"project_id": "p1"})

    assert body["result"].get("isError") is not True, body
    assert body["result"]["structuredContent"] == [{"project_id": "p1"}]


def test_passthrough_is_refused_where_the_conformance_server_would_have_carried_it() -> None:
    # The spec the ``REJECT`` tool serves, registered under ``PASSTHROUGH``:
    # refused at registration, so no call can reach it.
    server = MCPServer(name="conformance", auth_backend=AllowAnyBackend())

    with pytest.raises(ImproperlyConfigured, match="PASSTHROUGH is not honoured by the kernel"):
        server.register_tool(
            _spec(),
            name="conformance.passthrough_unknown",
            unknown_arguments=UnknownArguments.PASSTHROUGH,
        )

    assert server.tools.get("conformance.passthrough_unknown") is None
