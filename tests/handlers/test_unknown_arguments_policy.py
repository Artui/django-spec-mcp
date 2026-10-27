"""The ``UnknownArguments`` policy of a kernel tool, on the call and in ``tools/list``.

A kernel tool's policy is handed to ``dispatch``, whose closed argument set
refuses or drops a key ``spec.parameters()`` does not declare, so the policy
reaches two layers from one place: the call, and the ``inputSchema``'s
``additionalProperties``. These tests pin both from a real ``MCPServer``, on a
service tool and a paginated ``LIST`` selector tool, whose ``page`` and
``limit`` are arguments the transport declares beside the spec's.

The drf-services original asked this of an ``input_serializer`` and the
drf-services dispatch; it is kept beside this file as
``test_unknown_arguments_policy_drfs.py``. Changed on the kernel path, by the
kernel's rules:

- ``PASSTHROUGH`` is refused at registration, because no argument the spec
  does not declare ever reaches a kernel callable.
- A seed's name the caller sends is refused under ``REJECT`` like any other
  undeclared key, and dropped under ``IGNORE`` so the seed's value is the one
  the callable reads. The drf-services path admitted ``user``, ``request``,
  ``data`` and the names it drops at its edge under ``REJECT`` and dropped
  them silently; the kernel has no edge seeds to drop.
- An unknown key's refusal is keyed by its own name, where drf-services wrote
  a sentence under ``non_field_errors``.

Not asked, because the kernel has no counterpart: a selector tool's
``input_serializer``, a dataclass one wrapped for it, a bare ``**kwargs``
selector standing open, ``ordering`` as a transport knob and a nested
selector's key set. Which keys a kernel spec admits is ``spec.parameters()``,
and ``tests/conformance/test_unknown_arguments_matrix.py`` holds the schema's
agreement with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DataclassValidator,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    UnknownArguments,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from tests.utils import tool_error

pytestmark = pytest.mark.django_db(transaction=True)

OPEN = [Unrestricted()]


@dataclass
class _Known:
    known: str


def _server(policy: UnknownArguments, seen: dict[str, Any]) -> MCPServer:
    """A service tool and a paginated list tool under ``policy``, recording what reaches each.

    Both callables take a bare ``**kwargs``, so a key reaching either is seen
    whatever its name, and a test asserting it absent asserts the policy.
    """

    def _service(**kwargs: Any) -> dict[str, bool]:
        seen.update(kwargs)
        return {"ok": True}

    def _rows(**kwargs: Any) -> list[dict[str, int]]:
        seen.update(kwargs)
        return [{"id": 1}, {"id": 2}]

    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(
        ServiceSpec(service=_service, validator=DataclassValidator(_Known), permissions=OPEN),
        name="service",
        unknown_arguments=policy,
    )
    server.register_tool(
        SelectorSpec(kind=SelectorKind.LIST, selector=_rows, permissions=OPEN),
        name="rows",
        unknown_arguments=policy,
        paginate=True,
    )
    return server


def _schemas(server: MCPServer) -> dict[str, dict[str, Any]]:
    listed: Any = server.list_tools(user=None)
    return {tool["name"]: tool["inputSchema"] for tool in listed["tools"]}


# ---------- tools/list ----------


def test_reject_closes_the_schema() -> None:
    schemas = _schemas(_server(UnknownArguments.REJECT, {}))

    assert schemas["service"]["additionalProperties"] is False
    assert schemas["rows"]["additionalProperties"] is False
    assert set(schemas["rows"]["properties"]) == {"page", "limit"}


def test_ignore_leaves_the_schema_open() -> None:
    schemas = _schemas(_server(UnknownArguments.IGNORE, {}))

    assert schemas["service"].get("additionalProperties", True) is not False
    assert schemas["rows"].get("additionalProperties", True) is not False


def test_passthrough_is_refused_at_registration() -> None:
    with pytest.raises(ImproperlyConfigured, match="PASSTHROUGH is not honoured"):
        _server(UnknownArguments.PASSTHROUGH, {})


# ---------- tools/call ----------


@pytest.mark.parametrize(
    ("name", "arguments"),
    [("service", {"known": "ok", "rogue": 1}), ("rows", {"rogue": 1})],
)
async def test_reject_refuses_an_unknown_key_with_a_validation_error_result(
    name: str, arguments: dict[str, Any]
) -> None:
    seen: dict[str, Any] = {}

    out = await _server(UnknownArguments.REJECT, seen).acall_tool(name, arguments, user=None)

    error = tool_error(out)
    assert error["type"] == "validation_error"
    assert error["detail"] == {"rogue": ["Unknown argument."]}
    assert seen == {}


async def test_reject_accepts_only_known_keys() -> None:
    seen: dict[str, Any] = {}

    out = await _server(UnknownArguments.REJECT, seen).acall_tool(
        "service", {"known": "ok"}, user=None
    )

    assert out["structuredContent"] == {"ok": True}
    assert seen["known"] == "ok"


async def test_reject_treats_the_page_arguments_as_known() -> None:
    # The transport's own arguments, read off before dispatch, so the
    # kernel's closed set never sees them.
    out = await _server(UnknownArguments.REJECT, {}).acall_tool(
        "rows", {"page": 1, "limit": 1}, user=None
    )

    assert out["structuredContent"]["items"] == [{"id": 1}]
    assert out["structuredContent"]["hasNext"] is True


@pytest.mark.parametrize(
    ("name", "arguments"),
    [("service", {"known": "ok", "rogue": 1}), ("rows", {"rogue": 1})],
)
async def test_ignore_drops_an_unknown_key(name: str, arguments: dict[str, Any]) -> None:
    seen: dict[str, Any] = {}

    out = await _server(UnknownArguments.IGNORE, seen).acall_tool(name, arguments, user=None)

    assert "isError" not in out, out
    assert "rogue" not in seen


@pytest.mark.parametrize("name", ["user", "data"])
async def test_reject_refuses_a_seeds_name_the_caller_sends(name: str) -> None:
    seen: dict[str, Any] = {}

    out = await _server(UnknownArguments.REJECT, seen).acall_tool(
        "service", {"known": "ok", name: "evil"}, user=None
    )

    assert tool_error(out)["detail"] == {name: ["Unknown argument."]}
    assert seen == {}


@pytest.mark.parametrize("name", ["user", "data"])
async def test_ignore_drops_a_seeds_name_so_the_seed_is_read(name: str) -> None:
    seen: dict[str, Any] = {}

    out = await _server(UnknownArguments.IGNORE, seen).acall_tool(
        "service", {"known": "ok", name: "evil"}, user=None
    )

    assert "isError" not in out, out
    assert seen[name] != "evil"
