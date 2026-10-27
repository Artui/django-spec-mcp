"""The drf-spectacular recipe's claims, held against the schema a tool advertises.

The overrides are read by drf-services, whose schema builder ``build_input_schema``
wraps, so this package carries no spectacular code of its own and no extra for
it. What it does carry is the recipe saying a tool's ``inputSchema`` honours
them, and these tests are what would fail if a drf-services release stopped
doing so. The serializers are the recipe's own.
"""

from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, extend_schema_field, extend_schema_serializer
from rest_framework import serializers

from django_spec_mcp.schema.input_schema import build_input_schema


@extend_schema_serializer(
    exclude_fields=["internal_id"],
    deprecate_fields=["legacy"],
    examples=[
        OpenApiExample("Typical invoice", value={"amount": 4200, "currency": "USD"}),
        # A placeholder with no ``value=`` is filtered out of ``examples``.
        OpenApiExample("Placeholder"),
    ],
)
class InvoiceInput(serializers.Serializer):
    amount = serializers.IntegerField()
    currency = serializers.ChoiceField(choices=[("USD", "USD"), ("EUR", "EUR")])
    legacy = serializers.CharField(required=False)
    internal_id = serializers.IntegerField(required=False)


@extend_schema_field({"type": "string", "format": "iban", "minLength": 15})
class IBANField(serializers.CharField):
    pass


@extend_schema_field(OpenApiTypes.UUID)
class EnumAnnotatedField(serializers.CharField):
    pass


class TransferInput(serializers.Serializer):
    from_account = IBANField()
    reference = EnumAnnotatedField()


class _PlainReference(serializers.Serializer):
    reference = serializers.CharField()


def test_class_level_overrides_reach_the_input_schema() -> None:
    schema = build_input_schema(InvoiceInput)

    assert "internal_id" not in schema["properties"]
    assert "internal_id" not in schema.get("required", [])
    assert schema["properties"]["legacy"]["deprecated"] is True
    assert schema["examples"] == [{"amount": 4200, "currency": "USD"}]


def test_a_dict_field_override_replaces_the_derived_fragment() -> None:
    schema = build_input_schema(TransferInput)

    assert schema["properties"]["from_account"] == {
        "type": "string",
        "format": "iban",
        "minLength": 15,
    }


def test_a_non_dict_field_override_falls_through_to_the_derived_fragment() -> None:
    schema = build_input_schema(TransferInput)

    assert (
        schema["properties"]["reference"]
        == build_input_schema(_PlainReference)["properties"]["reference"]
    )
