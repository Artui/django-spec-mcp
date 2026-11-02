"""A ``many`` kernel service tool's ``inputSchema``.

The list travels under ``spec.many_argument``, so the advertised input is an
object with that one array property, each row the Validator's fields. The
shape is the kernel's ``spec_caller_input_schema``, taken rather than rebuilt;
the spec's own ``reads`` sit beside the list, as the call takes them. Each row
asks only what the caller must send: a field ``input_data`` always fills is not
offered, and one it may fill is not required.

An argument the service takes beside the list is declared as a read.
"""

from __future__ import annotations

from typing import Any

from django import forms
from django_service_specs import (
    DataclassValidator,
    FormValidator,
    Parameter,
    Parameters,
    ServiceSpec,
    Unrestricted,
    UnsetType,
    spec_input_schema,
)
from typing_extensions import TypedDict

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from tests.testapp.kernel_shapes import InvoiceInput


def _bulk(**overrides: Any) -> ServiceSpec:
    declared: dict[str, Any] = {
        "service": lambda **_: None,
        "atomic": False,
        "many": True,
        "validator": DataclassValidator(InvoiceInput),
        "permissions": [Unrestricted()],
        **overrides,
    }
    return ServiceSpec(**declared)


def _schema(spec: ServiceSpec) -> dict[str, Any]:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(spec, name="bulk", description="Record invoices.")
    listed: Any = server.list_tools(user=None)
    (tool,) = listed["tools"]
    return tool["inputSchema"]


def test_the_list_is_advertised_under_the_default_argument() -> None:
    schema = _schema(_bulk())

    assert schema == {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "number": {"type": "string"},
                        "amount_cents": {"type": "integer", "default": 0},
                    },
                    "required": ["number"],
                    "additionalProperties": False,
                },
                # The kernel's default bound on a ``many`` call's rows.
                "maxItems": 1000,
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


def test_a_declared_argument_name_is_the_property_advertised() -> None:
    schema = _schema(_bulk(many_argument="rows"))

    assert list(schema["properties"]) == ["rows"]
    assert schema["required"] == ["rows"]


def test_the_shape_is_the_kernels_verbatim() -> None:
    """The list's bounds are declared on the spec, and reach the schema as the kernel writes them.

    The bounds are the spec's ``many_min_length`` and ``many_max_length``.
    """
    spec = _bulk(many_min_length=1, many_max_length=50)

    schema = _schema(spec)

    assert schema == spec_input_schema(spec)
    assert schema["properties"]["items"]["minItems"] == 1
    assert schema["properties"]["items"]["maxItems"] == 50


def test_a_read_sits_beside_the_list() -> None:
    # An argument the service reads beside the rows is one of its ``reads``.
    spec = _bulk(
        service=lambda *, project_pk, **_: None,
        reads=Parameters([Parameter("project_pk", "integer", required=True)]),
    )

    schema = _schema(spec)

    assert list(schema["properties"]) == ["project_pk", "items"]
    assert schema["required"] == ["project_pk", "items"]


class _NoteForm(forms.Form):
    # A partial call needs a Validator that binds only what was sent; the
    # kernel refuses ``partial`` beside a ``DataclassValidator``.
    note = forms.CharField()


def test_partial_relaxes_the_item_and_never_the_argument() -> None:
    """``partial`` drops ``required`` inside each row; the list itself is still
    required, as dispatch refuses a call without it under ``partial`` too."""
    schema = _schema(_bulk(validator=FormValidator(_NoteForm), partial=True))

    assert schema["required"] == ["items"]
    assert "required" not in schema["properties"]["items"]["items"]


def test_an_untyped_input_data_leaves_a_many_specs_list_required() -> None:
    # A ``many`` spec's Validator fields are each row's, not top-level
    # arguments, so a provider that may fill any of them relaxes nothing at the
    # top level: not the list, and not a read that shares a row field's name,
    # which the kernel allows because the two never meet.
    spec = _bulk(
        service=lambda *, number, **_: None,
        reads=Parameters([Parameter("number", "string", required=True)]),
        input_data=lambda: {},
    )

    schema = _schema(spec)

    assert schema["required"] == ["number", "items"]


class _Numbered(TypedDict):
    number: str


class _MaybeNumbered(TypedDict):
    number: str | UnsetType


def _numbered() -> _Numbered:
    return {"number": "A-1"}


def _maybe_numbered() -> _MaybeNumbered:
    return {"number": "A-1"}


def _row(schema: dict[str, Any]) -> dict[str, Any]:
    return schema["properties"]["items"]["items"]


def test_a_row_field_a_typed_input_data_fills_is_neither_offered_nor_required() -> None:
    # ``input_data`` is merged over every row, and dispatch drops a caller's
    # value for a field it is annotated to fill, so no row asks for it. With
    # ``number`` gone the row requires nothing, and writes no empty ``required``.
    row = _row(_schema(_bulk(input_data=_numbered)))

    assert row["properties"] == {"amount_cents": {"type": "integer", "default": 0}}
    assert "required" not in row


def test_a_row_field_a_typed_input_data_may_decline_is_offered_and_not_required() -> None:
    row = _row(_schema(_bulk(input_data=_maybe_numbered)))

    assert set(row["properties"]) == {"number", "amount_cents"}
    assert "required" not in row


def test_beside_an_untyped_input_data_a_row_requires_nothing() -> None:
    # A provider not annotated with a ``TypedDict`` may fill any field and fills
    # none for certain: every field is offered, none required.
    row = _row(_schema(_bulk(input_data=lambda: {})))

    assert set(row["properties"]) == {"number", "amount_cents"}
    assert "required" not in row


def test_a_rows_relaxing_stays_out_of_the_top_level() -> None:
    # The list and a read beside it are demanded of the caller whatever
    # ``input_data`` reaches inside a row, including a read sharing a row
    # field's name.
    spec = _bulk(
        service=lambda *, number, project_pk, **_: None,
        reads=Parameters(
            [
                Parameter("number", "string", required=True),
                Parameter("project_pk", "integer", required=True),
            ]
        ),
        input_data=lambda: {},
    )

    schema = _schema(spec)

    assert "required" not in _row(schema)
    assert list(schema["properties"]) == ["number", "project_pk", "items"]
    assert schema["required"] == ["number", "project_pk", "items"]
