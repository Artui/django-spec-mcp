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
from tests.utils import make_server, tool_error

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
    server = make_server(session_store=None)
    server.register_tool(spec, name="t", unknown_arguments=policy)
    return server


def _input_schema(server: MCPServer) -> dict[str, Any]:
    listed: Any = server.list_tools(user=None)
    return listed["tools"][0]["inputSchema"]


def _call(server: MCPServer, arguments: dict[str, Any]) -> dict[str, Any]:
    return server.call_tool("t", arguments, user=None).to_dict()


def test_a_target_lookup_service_refuses_an_undeclared_argument() -> None:
    # ``archive_task`` behind a ``pk`` lookup, under the default ``REJECT``. The lookup's ``pk``
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
    server = _server(_SPECS["validator"](), policy=UnknownArguments.IGNORE)

    schema = _input_schema(server)
    out = _call(server, {"title": "kept", "typo": "dropped"})

    assert set(schema["properties"]) == {"title", "note"}
    assert "additionalProperties" not in schema
    assert out.get("isError") is not True, out
    assert out["structuredContent"] == {"title": "kept", "note": ""}


def test_a_bare_var_keyword_service_stays_closed_and_receives_no_undeclared_argument() -> None:
    # An argument the spec does not declare never reaches a kernel callable, so
    server = _server(_SPECS["var-keyword"](), policy=UnknownArguments.REJECT)

    schema = _input_schema(server)
    refused = tool_error(_call(server, {"title": "t", "colour": "red"}))
    served = _call(server, {"title": "t"})

    assert schema["additionalProperties"] is False
    assert refused["detail"] == {"colour": ["Unknown argument."]}
    assert {"title", "note"} <= set(served["structuredContent"]["changes"])
