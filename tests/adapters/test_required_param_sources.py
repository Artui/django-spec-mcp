from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DataclassValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
    unreceived_fields,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend

_PK = Parameters([Parameter("pk", "integer", required=True)])


@dataclass
class _Titled:
    title: str
    note: str = ""


def _service(service: Any, **declared: Any) -> ServiceSpec:
    return ServiceSpec(service=service, atomic=False, permissions=[Unrestricted()], **declared)


def _retrieve(selector: Any, **declared: Any) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=selector, permissions=[Unrestricted()], **declared
    )


def _server(spec: Any) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(spec, name="t", description="t")
    return server


def _served(spec: Any, arguments: dict[str, Any]) -> Any:
    # The text block carries every result; structured content mirrors it for an
    out = _server(spec).call_tool("t", arguments, user=None).to_dict()
    assert out.get("isError") is not True, out
    served = json.loads(out["content"][0]["text"])
    if isinstance(served, dict):
        assert out["structuredContent"] == served
    else:
        assert "structuredContent" not in out
    return served


def test_a_required_seed_parameter_is_filled_by_dispatch() -> None:
    assert _served(_service(lambda *, user: user.is_anonymous), {}) is True


def test_a_required_data_parameter_without_a_validator_receives_an_empty_payload() -> None:
    # The kernel always seeds ``data`` for a service, so a spec with no
    assert _served(_service(lambda *, data: dict(data)), {}) == {}


def test_the_calls_own_view_and_context_reach_a_required_parameter() -> None:
    served = _served(_service(lambda *, view, call_context: [view, sorted(call_context)]), {})

    assert served == {"items": [None, ["action", "format", "request", "transport", "view"]]}


def test_a_required_parameter_a_read_declares_is_the_callers() -> None:
    assert _served(_retrieve(lambda *, pk: pk, reads=_PK), {"pk": 7}) == 7


def test_a_required_parameter_a_provider_fills_needs_no_argument() -> None:
    assert _served(_retrieve(lambda *, pk: pk, kwargs=lambda: {"pk": 3}), {}) == 3


def test_a_defaulted_parameter_nothing_fills_runs_on_its_default() -> None:
    assert _served(_retrieve(lambda *, pk=5: pk), {}) == 5


def test_a_var_positional_parameter_needs_no_source() -> None:
    # ``*tags`` has no default, so a check counting every parameter would call it
    assert _served(_service(lambda *tags: {"tags": list(tags)}), {}) == {"tags": []}


def test_a_defaulted_positional_only_parameter_registers_and_runs_on_its_default() -> None:
    # Positional-only cannot be bound by keyword, so a check reading the name
    assert _served(_retrieve(lambda pk=5, /: {"pk": pk}), {}) == {"pk": 5}


@pytest.mark.parametrize(
    "service",
    [
        pytest.param(lambda *, title, note: note, id="by-name"),
        pytest.param(lambda *, data: data.note, id="through-data"),
        pytest.param(lambda **kwargs: kwargs["note"], id="through-kwargs"),
    ],
)
def test_a_validator_field_reaches_a_callable_that_can_receive_it(
    service: Callable[..., Any],
) -> None:
    # What the refusal below must leave alone: a field is not lost when the
    spec = _service(service, validator=DataclassValidator(_Titled))

    assert _served(spec, {"title": "a", "note": "kept"}) == "kept"


def test_a_selector_requiring_the_view_registers_and_is_handed_it() -> None:
    # The kernel's in-process route has no view, so ``view`` is ``None``; what
    seen: list[Any] = []

    def _by_view(*, view: Any) -> dict[str, bool]:
        seen.append(view)
        return {"ran": True}

    assert _served(_retrieve(_by_view), {}) == {"ran": True}
    assert seen == [None]


# The kernel's wording for a keyword no read, seed or default fills.
_UNFILLED = "which nothing fills"


@pytest.mark.parametrize(
    ("build", "wording"),
    [
        # Factories, built inside the block, so a spec refused when it is built
        pytest.param(lambda: _service(lambda *, other: other), _UNFILLED, id="service"),
        pytest.param(lambda: _retrieve(lambda *, pk: pk), _UNFILLED, id="selector"),
        pytest.param(
            lambda: _service(lambda *, instance: instance),
            _UNFILLED,
            id="instance-without-a-lookup",
        ),
        pytest.param(
            lambda: _retrieve(lambda pk, /: pk, reads=_PK),
            "positional-only parameter",
            id="positional-only",
        ),
        pytest.param(
            lambda: _retrieve(lambda *, data: data), _UNFILLED, id="selector-requiring-data"
        ),
    ],
)
def test_a_required_parameter_nothing_fills_is_refused_at_registration(
    build: Callable[[], Any], wording: str
) -> None:
    with pytest.raises(ImproperlyConfigured, match=rf"MCP tool 't': .*{wording}"):
        _server(build())


def test_a_validator_field_the_callable_cannot_receive_registers_and_is_validated() -> None:
    # ``note`` reaches a callable naming it, taking ``data`` or taking ``**kwargs``;
    spec = _service(lambda *, title: title, validator=DataclassValidator(_Titled))
    server = _server(spec)
    listed: Any = server.list_tools(user=None)

    out = server.call_tool("t", {"title": "a", "note": "kept"}, user=None).to_dict()
    refused = server.call_tool("t", {"title": "a", "note": 5}, user=None).to_dict()

    assert unreceived_fields(spec) == frozenset({"note"})
    assert "note" in listed["tools"][0]["inputSchema"]["properties"]
    assert out.get("isError") is not True, out
    assert json.loads(out["content"][0]["text"]) == "a"
    assert refused.get("isError") is True, refused
    assert json.loads(refused["content"][0]["text"])["error"] == {
        "type": "validation_error",
        "message": "Invalid arguments",
        "detail": {"note": ["Expected a string."]},
    }
