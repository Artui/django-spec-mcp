"""What fills a kernel callable's required parameter, and what registration does when nothing does.

A kernel service's callable receives, by name, what ``dispatch`` puts in its
pool: the dispatcher's seeds (``user``, ``data``, ``call_context`` and the
rest), the call's own ``request`` and ``view``, the spec's declared ``reads``
and Validator fields, and what a ``kwargs=`` provider returns. A selector is
handed the same except ``data``, which the kernel seeds for a service only.
Each of those is asked here to fill a required parameter of a kernel spec
registered with ``register_tool``, end to end through ``call_tool``. A
var-positional parameter and a defaulted one need no source, and are asked
here so a refusal of the unfilled cases cannot reach them.

A required parameter none of them fills was refused at registration on the
drf-services path, because every call to such a tool raises ``TypeError`` out of
dispatch, and it is refused at registration here too: ``register_tool`` asks the
kernel's ``check_seeds`` with the server's ``pool_seeds``, since the registered
pool is not known when a spec is built (``lambda *, tenant: tenant`` is valid on
a server whose pool registers ``tenant``). A Validator field the callable has
no way to receive registers, is advertised, and is validated on the call,
which runs without it. The kernel answers such a field with
``unreceived_fields`` and does not refuse it, because such a spec runs and may
mean to (a Validator checking that two fields agree passes one of them on), and
this package does not refuse it either. The drf-services twin that refused it is
``test_input_serializer_validation_drfs.py``.

The drf-services original asked this of trust mode, ``ArgumentBinding``,
``UrlKwarg`` sources and an ``input_serializer``'s fields under each binding;
none has a kernel counterpart, since a kernel spec declares its arguments. It
is kept beside this file as ``test_required_param_sources_drfs.py``.
"""

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
    out = _server(spec).call_tool("t", arguments, user=None).to_dict()
    assert out.get("isError") is not True, out
    return out["structuredContent"]


def test_a_required_seed_parameter_is_filled_by_dispatch() -> None:
    assert _served(_service(lambda *, user: user.is_anonymous), {}) is True


def test_a_required_data_parameter_without_a_validator_receives_an_empty_payload() -> None:
    # Unlike the drf-services path, which refused a bundled service requiring
    # ``data`` with no ``input_serializer``: the kernel always seeds ``data``.
    assert _served(_service(lambda *, data: dict(data)), {}) == {}


def test_the_calls_own_view_and_context_reach_a_required_parameter() -> None:
    served = _served(_service(lambda *, view, call_context: [view, sorted(call_context)]), {})

    assert served == [None, ["action", "format", "request", "view"]]


def test_a_required_parameter_a_read_declares_is_the_callers() -> None:
    assert _served(_retrieve(lambda *, pk: pk, reads=_PK), {"pk": 7}) == 7


def test_a_required_parameter_a_provider_fills_needs_no_argument() -> None:
    assert _served(_retrieve(lambda *, pk: pk, kwargs=lambda: {"pk": 3}), {}) == 3


def test_a_defaulted_parameter_nothing_fills_runs_on_its_default() -> None:
    assert _served(_retrieve(lambda *, pk=5: pk), {}) == 5


def test_a_var_positional_parameter_needs_no_source() -> None:
    # ``*tags`` has no default, so a check counting every parameter would call it
    # required with no source; dispatch binds by keyword and leaves it empty.
    assert _served(_service(lambda *tags: {"tags": list(tags)}), {}) == {"tags": []}


def test_a_defaulted_positional_only_parameter_registers_and_runs_on_its_default() -> None:
    # Positional-only cannot be bound by keyword, so a check reading the name
    # alone would find no source for it; the default is what answers.
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
    # callable names it, takes ``data`` or takes ``**kwargs``.
    spec = _service(service, validator=DataclassValidator(_Titled))

    assert _served(spec, {"title": "a", "note": "kept"}) == "kept"


def test_a_selector_requiring_the_view_registers_and_is_handed_it() -> None:
    # The kernel's in-process route has no view, so ``view`` is ``None``; what
    # is asked is that the parameter is filled rather than left to ``TypeError``.
    # The selector returns a value, since a retrieve answering ``None`` is a miss.
    seen: list[Any] = []

    def _by_view(*, view: Any) -> dict[str, bool]:
        seen.append(view)
        return {"ran": True}

    assert _served(_retrieve(_by_view), {}) == {"ran": True}
    assert seen == [None]


@pytest.mark.parametrize(
    "build",
    [
        # Factories, built inside the block, so a spec refused when it is built
        # cannot fail collection; the match on the tool's name says the
        # registration refused it, not the spec's construction.
        pytest.param(lambda: _service(lambda *, other: other), id="service"),
        pytest.param(lambda: _retrieve(lambda *, pk: pk), id="selector"),
        pytest.param(
            lambda: _service(lambda *, instance: instance), id="instance-without-a-lookup"
        ),
        pytest.param(lambda: _retrieve(lambda pk, /: pk, reads=_PK), id="positional-only"),
        # The kernel seeds ``data`` for a service only, so a selector requiring it
        # is as unfilled as ``other``.
        pytest.param(lambda: _retrieve(lambda *, data: data), id="selector-requiring-data"),
    ],
)
def test_a_required_parameter_nothing_fills_is_refused_at_registration(
    build: Callable[[], Any],
) -> None:
    with pytest.raises(ImproperlyConfigured, match=r"MCP tool 't': "):
        _server(build())


def test_a_validator_field_the_callable_cannot_receive_registers_and_is_validated() -> None:
    # ``note`` reaches a callable naming it, taking ``data`` or taking ``**kwargs``;
    # this one takes ``title`` alone, so ``note`` is validated and not passed on.
    spec = _service(lambda *, title: title, validator=DataclassValidator(_Titled))
    server = _server(spec)
    listed: Any = server.list_tools(user=None)

    out = server.call_tool("t", {"title": "a", "note": "kept"}, user=None).to_dict()
    refused = server.call_tool("t", {"title": "a", "note": 5}, user=None).to_dict()

    assert unreceived_fields(spec) == frozenset({"note"})
    assert "note" in listed["tools"][0]["inputSchema"]["properties"]
    assert out.get("isError") is not True, out
    assert out["structuredContent"] == "a"
    # A refusal naming no missing argument keeps the generic message.
    assert refused.get("isError") is True, refused
    assert json.loads(refused["content"][0]["text"])["error"] == {
        "type": "validation_error",
        "message": "Invalid arguments",
        "detail": {"note": ["Expected a string."]},
    }
