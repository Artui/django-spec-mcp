"""A kernel service tool's target selector resolves the row its service acts on.

A kernel ``ServiceSpec`` carries an ``instance_selector_spec`` and ``partial``:
the first is a ``RETRIEVE`` ``SelectorSpec`` whose
``reads`` name the lookup, and dispatch hands the resolved row to the
``Validator``'s context and to the service as ``instance``. Asked here, on both
handlers: the service receives the row, a
missing one (``None``, an empty queryset, ``DoesNotExist``) is a tool-level
``not_found``, a non-queryset return is used as it is, the lookup's own
``annotations`` and ``kwargs`` provider shape it, a caller cannot stand in for
a seed, and ``partial`` relaxes what the Validator requires.

A kernel Validator hands the service its validated values. The lookup key
declared again by the body is refused when the tool is registered.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet, Value
from django.http import HttpRequest
from django_service_specs import (
    DataclassValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
    Validated,
    ValidationContext,
    Validator,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_tools_call import handle_tools_call
from django_spec_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from tests.testapp.models import Invoice
from tests.utils import tool_error

pytestmark = pytest.mark.django_db(transaction=True)

OPEN = [Unrestricted()]
_PK = Parameters((Parameter("pk", "integer", required=True),))
routes = pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])


@dataclass
class _Number:
    number: str


@dataclass
class _Rename:
    pk: int
    number: str


def _invoice_by_pk(*, pk: int) -> QuerySet[Invoice]:
    return Invoice.objects.filter(pk=pk)


def _lookup(selector: Any = _invoice_by_pk, **spec: Any) -> SelectorSpec:
    return SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector, reads=_PK, **spec)


def _rename_invoice(*, instance: Invoice, number: str) -> dict[str, Any]:
    instance.number = number
    instance.save(update_fields=["number"])
    return {"number": instance.number}


def _server(**spec: Any) -> MCPServer:
    fields: dict[str, Any] = {
        "service": _rename_invoice,
        "validator": DataclassValidator(_Number),
        "instance_selector_spec": _lookup(),
        "atomic": False,
        "permissions": OPEN,
        **spec,
    }
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(ServiceSpec(**fields), name="invoices.rename")
    return server


async def _call(server: MCPServer, arguments: dict[str, Any], *, is_async: bool) -> Any:
    params: dict[str, Any] = {"name": "invoices.rename", "arguments": arguments}
    context = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )
    if is_async:
        return await handle_tools_call_async(params, context)
    return await sync_to_async(handle_tools_call)(params, context)


def test_a_lookup_key_the_validator_also_declares_is_refused_at_registration() -> None:
    # A body field cannot name the row the target lookup resolves. The spec
    # itself builds; registering it asks its parameters, which refuse the pair.
    with pytest.raises(ImproperlyConfigured, match=r"\['pk'\] are declared by two sources"):
        _server(validator=DataclassValidator(_Rename))


def _strict_get(*, pk: int) -> Invoice:
    return Invoice.objects.get(pk=pk)


@routes
@pytest.mark.parametrize(
    "selector",
    [_invoice_by_pk, _strict_get, lambda *, pk: None],
    ids=["empty-queryset", "does-not-exist", "none"],
)
async def test_a_missing_row_is_a_tool_level_not_found(selector: Any, is_async: bool) -> None:
    server = _server(instance_selector_spec=_lookup(selector))

    out = await _call(server, {"pk": 99999, "number": "new"}, is_async=is_async)

    assert tool_error(out) == {
        "type": "not_found",
        "message": "invoices.rename: no matching instance found",
    }


@routes
@pytest.mark.parametrize(
    "selector", [_invoice_by_pk, _strict_get], ids=["queryset", "non-queryset-used-as-it-is"]
)
async def test_the_service_receives_the_resolved_row(selector: Any, is_async: bool) -> None:
    invoice = await Invoice.objects.acreate(number="orig")

    out = await _call(
        _server(instance_selector_spec=_lookup(selector)),
        {"pk": invoice.pk, "number": "new"},
        is_async=is_async,
    )

    assert "isError" not in out, out
    await invoice.arefresh_from_db()
    assert invoice.number == "new"


def test_the_lookups_shaping_reaches_the_row() -> None:
    invoice = Invoice.objects.create(number="orig")
    seen: dict[str, Any] = {}

    def service(*, instance: Any) -> None:
        seen["marker"] = instance.marker

    server = _server(
        service=service,
        validator=None,
        instance_selector_spec=_lookup(annotations={"marker": Value(42)}),
    )

    out = server.call_tool("invoices.rename", {"pk": invoice.pk}, user=None).to_dict()

    assert "isError" not in out, out
    assert seen["marker"] == 42


def test_the_lookups_kwargs_provider_scopes_the_row() -> None:
    # The provider fills ``only_sent``, which no read declares, so the lookup
    # finds a sent row, refuses an unsent one, and a caller naming the value
    # is refused for an argument the tool does not take.
    unsent = Invoice.objects.create(number="unsent", sent=False)
    sent = Invoice.objects.create(number="sent", sent=True)

    def scoped(*, pk: int, only_sent: bool) -> QuerySet[Invoice]:
        return Invoice.objects.filter(pk=pk, sent=only_sent)

    server = _server(
        instance_selector_spec=_lookup(scoped, kwargs=lambda: {"only_sent": True}),
    )

    def call(**arguments: Any) -> dict[str, Any]:
        return server.call_tool("invoices.rename", arguments, user=None).to_dict()

    assert call(pk=sent.pk, number="x")["structuredContent"] == {"number": "x"}
    assert tool_error(call(pk=unsent.pk, number="x"))["type"] == "not_found"
    refused = tool_error(call(pk=unsent.pk, number="x", only_sent=False))
    assert refused["type"] == "validation_error"
    assert "only_sent" in refused["detail"]


class _RecordingValidator(DataclassValidator):
    """Validates as ``DataclassValidator`` does, recording the row in its context."""

    def __init__(self) -> None:
        super().__init__(_Number)
        self.targets: list[Any] = []

    def validated(self, arguments: Mapping[str, Any], context: ValidationContext) -> Validated:
        self.targets.append(context.target)
        return super().validated(arguments, context)


def test_the_validator_sees_the_resolved_row() -> None:
    invoice = Invoice.objects.create(number="orig")
    validator = _RecordingValidator()
    server = _server(validator=validator)

    out = server.call_tool("invoices.rename", {"pk": invoice.pk, "number": "new"}, user=None)

    assert "isError" not in out.to_dict()
    assert validator.targets == [invoice]


@routes
async def test_a_caller_cannot_stand_in_for_a_seed(is_async: bool) -> None:
    # ``instance`` and ``data`` are the dispatcher's, so a caller naming them
    # is refused for arguments the spec does not declare, and the row is
    # never touched.
    invoice = await Invoice.objects.acreate(number="orig")

    out = await _call(
        _server(),
        {"pk": invoice.pk, "number": "new", "instance": "evil", "data": {"number": "evil"}},
        is_async=is_async,
    )

    assert tool_error(out)["type"] == "validation_error"
    await invoice.arefresh_from_db()
    assert invoice.number == "orig"


class _SentFields(Validator):
    """Binds only what was sent on a partial call, requiring both fields otherwise.

    ``DataclassValidator`` builds every field, so a spec declaring ``partial``
    over it is refused when built; a partial call needs a Validator that can
    bind a part.
    """

    def parameters(self) -> Parameters:
        return Parameters(
            (
                Parameter("title", "string", required=True),
                Parameter("note", "string", required=True),
            )
        )

    def validate(self, arguments: Mapping[str, Any], context: ValidationContext) -> dict[str, Any]:
        return dict(arguments)


def _captured(captured: dict[str, Any]) -> Any:
    def service(*, data: Any) -> None:
        captured["data"] = data

    return service


def test_partial_relaxes_what_the_validator_requires() -> None:
    captured: dict[str, Any] = {}
    server = _server(
        service=_captured(captured),
        validator=_SentFields(),
        instance_selector_spec=None,
        partial=True,
    )

    out = server.call_tool("invoices.rename", {"note": "n"}, user=None).to_dict()

    assert "isError" not in out, out
    assert captured["data"] == {"note": "n"}


def test_without_partial_every_required_field_is_refused_at_once() -> None:
    server = _server(service=_captured({}), validator=_SentFields(), instance_selector_spec=None)

    error = tool_error(server.call_tool("invoices.rename", {}, user=None).to_dict())

    assert error["type"] == "validation_error"
    assert {"title", "note"} <= set(error["detail"])
