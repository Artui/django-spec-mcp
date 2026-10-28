"""A kernel service tool advertises the lookup its target selector reads.

A kernel service spec acting on a row or a set of rows names the selector that
finds them (``instance_selector_spec`` or ``collection_selector_spec``), and
that selector's ``reads`` are arguments of the call like the Validator's
fields: ``ServiceSpec.parameters()`` lists them first, and dispatch admits and
checks them. So the ``inputSchema`` lists them too, or a client validating its
arguments against it could never send the one call that works.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jsonschema
import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    DataclassPresenter,
    DataclassValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
    UnsetType,
)
from typing_extensions import TypedDict

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from tests.testapp.kernel_shapes import InvoiceOutput
from tests.testapp.models import Invoice


@dataclass
class _Rename:
    number: str


def _invoice_by_pk(*, pk: int) -> Any:
    return Invoice.objects.filter(pk=pk)


def _invoices_by_ids(*, ids: list[int]) -> Any:
    return Invoice.objects.filter(pk__in=ids)


def _rename(*, instance: Invoice, number: str) -> Invoice:
    instance.number = number
    instance.save(update_fields=["number"])
    return instance


def _rename_all(*, collection: Any, number: str) -> None:
    # The collection lookup seeds ``collection``, never ``instance``.
    collection.update(number=number)


_PK = Parameter("pk", "integer", required=True)


def _by_pk(*reads: Parameter, kwargs: Any = None) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_invoice_by_pk,
        reads=Parameters(list(reads) or [_PK]),
        kwargs=kwargs,
    )


def _rename_spec(**overrides: Any) -> ServiceSpec:
    declared: dict[str, Any] = {
        "service": _rename,
        "atomic": False,
        "permissions": [Unrestricted()],
        "validator": DataclassValidator(_Rename),
        "instance_selector_spec": _by_pk(),
        "presenter": DataclassPresenter(InvoiceOutput),
        **overrides,
    }
    return ServiceSpec(**declared)


def _server(spec: ServiceSpec) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(spec, name="rename_invoice", description="Rename an invoice.")
    return server


def _schema(spec: ServiceSpec) -> dict[str, Any]:
    listed: Any = _server(spec).list_tools(user=None)
    (tool,) = listed["tools"]
    return tool["inputSchema"]


def test_the_instance_selectors_lookup_is_advertised() -> None:
    schema = _schema(_rename_spec())

    assert set(schema["properties"]) == {"number", "pk"}
    assert schema["properties"]["pk"] == {"type": "integer"}


def test_the_collection_selectors_lookup_is_advertised() -> None:
    spec = _rename_spec(
        service=_rename_all,
        instance_selector_spec=None,
        collection_selector_spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_invoices_by_ids,
            reads=Parameters([Parameter("ids", "array", items="integer", required=True)]),
        ),
        presenter=None,
    )

    schema = _schema(spec)

    assert set(schema["properties"]) == {"number", "ids"}
    assert schema["properties"]["ids"]["type"] == "array"


def test_the_advertised_arguments_are_the_ones_the_call_admits() -> None:
    # The agreement asked of the kernel rather than restated: every parameter
    # the spec declares is a property, and the schema is closed beside them.
    spec = _rename_spec()

    schema = _schema(spec)

    assert set(schema["properties"]) == spec.parameters().names()
    assert schema["additionalProperties"] is False


def test_a_lookup_declared_required_is_required_beside_the_validators_fields() -> None:
    schema = _schema(_rename_spec())

    assert set(schema["required"]) == {"pk", "number"}


def _invoice_by_pk_or_none(*, pk: int | None = None) -> Any:
    return Invoice.objects.filter(pk=pk)


def test_a_lookup_declared_optional_adds_nothing_to_required() -> None:
    # The lookup's selector defaults ``pk``: an optional read over a keyword
    # with no default is refused at registration, since a call leaving it out
    # would be refused as missing.
    lookup = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_invoice_by_pk_or_none,
        reads=Parameters([Parameter("pk", "integer")]),
    )
    schema = _schema(_rename_spec(instance_selector_spec=lookup))

    assert schema["required"] == ["number"]


def test_a_validator_field_named_like_the_lookup_is_refused_at_registration() -> None:
    # An input field does not override the lookup of the same name. Two kernel
    # declarations of one name are refused when the spec's parameters are
    # assembled, which registration does to compile the schema, so no listing
    # ever shows either.
    @dataclass
    class _Pk:
        pk: int

    with pytest.raises(ImproperlyConfigured, match="pk"):
        _server(_rename_spec(validator=DataclassValidator(_Pk)))


class _Tenant(TypedDict):
    tenant: str


def _tenant_of_record() -> _Tenant:
    return {"tenant": "acme"}


def test_a_lookup_read_its_selectors_provider_fills_is_not_advertised() -> None:
    lookup = _by_pk(_PK, Parameter("tenant", "string", required=True), kwargs=_tenant_of_record)

    schema = _schema(_rename_spec(instance_selector_spec=lookup))

    assert set(schema["properties"]) == {"number", "pk"}
    assert set(schema["required"]) == {"number", "pk"}


def test_a_lookup_read_beside_an_untyped_provider_is_offered_and_not_required() -> None:
    # The lookup's provider reaches the lookup's reads and nothing else, so the
    # Validator's ``number`` stays required.
    lookup = _by_pk(kwargs=lambda: {})

    schema = _schema(_rename_spec(instance_selector_spec=lookup))

    assert set(schema["properties"]) == {"number", "pk"}
    assert schema["required"] == ["number"]


def test_a_lookup_read_a_registered_seed_fills_is_not_advertised() -> None:
    # A reflected read of the target's lookup is the server's once a seed of
    # its name is registered on the mount, as a reflected read of the spec's own is.
    lookup = _by_pk(_PK, Parameter("tenant", "string", required=True, reflected=True))
    server = MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=None,
        pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"),
    )
    server.register_tool(
        _rename_spec(instance_selector_spec=lookup), name="rename_invoice", description="Rename."
    )
    listed: Any = server.list_tools(user=None)

    (tool,) = listed["tools"]

    assert set(tool["inputSchema"]["properties"]) == {"number", "pk"}


class _MaybeTenant(TypedDict):
    tenant: str | UnsetType


def _maybe_tenant() -> _MaybeTenant:
    return {"tenant": "acme"}


def test_a_key_one_seat_may_decline_is_still_required_by_another() -> None:
    # The lookup's provider may answer ``tenant``, but the lookup does not read
    # it: the service's own read does, and no provider reaches that seat. So
    # dispatch demands ``tenant`` of the caller, and the schema requires it.
    spec = _rename_spec(
        service=lambda *, instance, number, tenant: instance,
        reads=Parameters([Parameter("tenant", "string", required=True)]),
        instance_selector_spec=_by_pk(kwargs=_maybe_tenant),
    )

    schema = _schema(spec)

    assert set(schema["properties"]) == {"number", "pk", "tenant"}
    assert set(schema["required"]) == {"number", "pk", "tenant"}


def test_a_spec_without_a_target_selector_advertises_only_its_input() -> None:
    schema = _schema(_rename_spec(service=lambda *, number: None, instance_selector_spec=None))

    assert set(schema["properties"]) == {"number"}


@pytest.mark.django_db
def test_a_call_valid_against_the_advertised_schema_renames_the_target() -> None:
    invoice = Invoice.objects.create(number="INV-1")
    server = _server(_rename_spec())
    listed: Any = server.list_tools(user=None)
    arguments = {"pk": invoice.pk, "number": "INV-2"}

    jsonschema.validate(arguments, listed["tools"][0]["inputSchema"])
    out = server.call_tool("rename_invoice", arguments, user=None).to_dict()

    assert out.get("isError") is not True, out
    assert out["structuredContent"]["number"] == "INV-2"
    invoice.refresh_from_db()
    assert invoice.number == "INV-2"
