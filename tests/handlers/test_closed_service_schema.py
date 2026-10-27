"""A kernel service tool advertises the arguments dispatch admits, and closes where it refuses.

``register_tool`` hands a service tool's ``unknown_arguments`` to the kernel's
``dispatch`` as registered. ``REJECT``, the default, refuses a name the spec
does not declare whatever the spec declares it through: a Validator, ``reads``,
a target selector's ``reads`` or nothing at all. The ``inputSchema`` lists
``ServiceSpec.parameters()`` and stamps ``additionalProperties: false`` exactly
where dispatch refuses an undeclared name; under ``IGNORE`` it carries no
``additionalProperties`` at all, which JSON Schema reads as open.

The drf-services original asked this per ``ArgumentBinding`` and of a
precondition hiding a lookup key behind ``NotClientInput``; a kernel spec has
one binding, and its arguments are what it declares, so neither has a kernel
counterpart. Two answers changed by design: the refusal names the key in the
detail (``{"rogue": ["Unknown argument."]}``) where drf-services filed it under
``non_field_errors``; and a service's bare ``**kwargs`` no longer opens the set,
since a kernel service receives only what the spec declares. It is kept beside
this file as ``test_closed_service_schema_drfs.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from django_service_specs import (
    DataclassValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    UnknownArguments,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from tests.utils import tool_error

# A value for every name any spec below can advertise, so a call can send
# exactly the advertised set.
_VALUES: dict[str, Any] = {"pk": 1, "reason": "duplicate", "title": "t", "note": "n"}


@dataclass
class _Title:
    title: str
    note: str = ""


def _get_task(*, pk: int) -> dict[str, Any]:
    return {"pk": pk}


def _archive_task(*, instance: dict[str, Any], reason: str = "") -> dict[str, Any]:
    return {"pk": instance["pk"], "reason": reason}


def _rename(*, title: str, note: str = "") -> dict[str, Any]:
    return {"title": title, "note": note}


def _touch(**changes: Any) -> dict[str, Any]:
    return {"changes": sorted(changes)}


def _spec(**declared: Any) -> ServiceSpec:
    return ServiceSpec(atomic=False, permissions=[Unrestricted()], **declared)


_SPECS: dict[str, Callable[[], ServiceSpec]] = {
    "validator": lambda: _spec(service=_rename, validator=DataclassValidator(_Title)),
    "reads": lambda: _spec(
        service=_rename,
        reads=Parameters(
            [Parameter("title", "string", required=True), Parameter("note", "string")]
        ),
    ),
    "var-keyword": lambda: _spec(service=_touch, validator=DataclassValidator(_Title)),
    "target-lookup": lambda: _spec(
        service=_archive_task,
        reads=Parameters([Parameter("reason", "string")]),
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_get_task,
            reads=Parameters([Parameter("pk", "integer", required=True)]),
        ),
    ),
    "no-input": lambda: _spec(service=lambda: {"ok": True}),
}


def _server(spec: ServiceSpec, *, policy: UnknownArguments) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(spec, name="t", unknown_arguments=policy)
    return server


def _input_schema(server: MCPServer) -> dict[str, Any]:
    listed: Any = server.list_tools(user=None)
    return listed["tools"][0]["inputSchema"]


def _call(server: MCPServer, arguments: dict[str, Any]) -> dict[str, Any]:
    return server.call_tool("t", arguments, user=None).to_dict()


def test_a_target_lookup_service_refuses_an_undeclared_argument() -> None:
    # The drf-services issue's reproduction, on the kernel: ``archive_task``
    # behind a ``pk`` lookup, under the default ``REJECT``. The lookup's ``pk``
    # and the service's ``reason`` are listed, and nothing else is admitted.
    server = _server(_SPECS["target-lookup"](), policy=UnknownArguments.REJECT)

    schema = _input_schema(server)
    refused = tool_error(_call(server, {"pk": 1, "reason": "duplicate", "notify_owner": True}))
    served = _call(server, {"pk": 1, "reason": "duplicate"})

    assert set(schema["properties"]) == {"pk", "reason"}
    assert schema["additionalProperties"] is False
    assert refused["type"] == "validation_error"
    assert refused["detail"] == {"notify_owner": ["Unknown argument."]}
    assert served.get("isError") is not True, served
    assert served["structuredContent"] == {"pk": 1, "reason": "duplicate"}


@pytest.mark.parametrize("policy", [UnknownArguments.REJECT, UnknownArguments.IGNORE])
@pytest.mark.parametrize("name", list(_SPECS))
def test_the_advertised_properties_are_the_keys_dispatch_admits(
    name: str, policy: UnknownArguments
) -> None:
    # The agreement asked of the kernel rather than restated: the properties
    # are the spec's parameters, and the schema is closed exactly where
    # dispatch refuses a name outside them. Closed, a call naming one
    # undeclared key is refused for that key; open, the same call is served.
    spec = _SPECS[name]()
    server = _server(spec, policy=policy)

    schema = _input_schema(server)
    closed = schema.get("additionalProperties") is False
    arguments = {key: _VALUES[key] for key in schema["properties"]}
    out = _call(server, {**arguments, "undeclared": 1})

    assert set(schema["properties"]) == spec.parameters().names()
    assert closed is (policy is UnknownArguments.REJECT)
    if closed:
        error = tool_error(out)
        assert error["type"] == "validation_error"
        assert error["detail"] == {"undeclared": ["Unknown argument."]}
    else:
        assert out.get("isError") is not True, out


def test_ignore_keeps_serving_an_undeclared_argument() -> None:
    # ``IGNORE`` is the opt-out: the schema lists the parameters and stays
    # open, and a call naming an undeclared key is served, with the declared
    # one delivered and the other dropped.
    server = _server(_SPECS["validator"](), policy=UnknownArguments.IGNORE)

    schema = _input_schema(server)
    out = _call(server, {"title": "kept", "typo": "dropped"})

    assert set(schema["properties"]) == {"title", "note"}
    assert "additionalProperties" not in schema
    assert out.get("isError") is not True, out
    assert out["structuredContent"] == {"title": "kept", "note": ""}


def test_a_bare_var_keyword_service_stays_closed_and_receives_only_the_declared() -> None:
    # Changed by design: drf-services read a spread service's ``**kwargs`` as
    # an open set. A kernel service receives what the spec declares, so its
    # ``**kwargs`` opens nothing: the undeclared key is refused, and a call
    # without it reaches the service with the Validator's fields beside the
    # pool's seeds, as any catch-all receives them.
    server = _server(_SPECS["var-keyword"](), policy=UnknownArguments.REJECT)

    schema = _input_schema(server)
    refused = tool_error(_call(server, {"title": "t", "colour": "red"}))
    served = _call(server, {"title": "t"})

    assert schema["additionalProperties"] is False
    assert refused["detail"] == {"colour": ["Unknown argument."]}
    assert {"title", "note"} <= set(served["structuredContent"]["changes"])
