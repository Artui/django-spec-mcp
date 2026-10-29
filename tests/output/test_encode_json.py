from __future__ import annotations

import importlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

import pytest

from django_spec_mcp.output.encode_json import encode_json


def test_encode_json_handles_decimal_uuid_datetime() -> None:
    out = encode_json(
        {
            "amount": Decimal("12.34"),
            "id": uuid.UUID("12345678-1234-5678-1234-567812345678"),
            "ts": datetime(2026, 4, 28, 12, 0, 0),
            "d": date(2026, 4, 28),
        }
    )
    parsed = json.loads(out)
    assert parsed["amount"] == "12.34"
    assert parsed["id"] == "12345678-1234-5678-1234-567812345678"
    assert parsed["ts"].startswith("2026-04-28")


def test_encode_json_sort_keys() -> None:
    out = encode_json({"b": 1, "a": 2})
    assert out.index('"a"') < out.index('"b"')


def test_encode_json_orders_integer_keys_by_value_and_ahead_of_names() -> None:
    # A kernel ``many`` call's detail node: rows by index, then the list's own
    # message. Not ``"10"`` before ``"2"``, and not a ``TypeError`` for the mix.
    node = {"non_field_errors": ["Too many."], 10: ["b"], 2: ["a"]}

    parsed = json.loads(encode_json({"items": node, "rows": ({"b": 1, "a": 2},)}))

    assert list(parsed["items"]) == ["2", "10", "non_field_errors"]
    assert list(parsed["rows"][0]) == ["a", "b"]


def test_encode_json_orders_a_mixed_node_by_number_then_by_name() -> None:
    # A float counts as a number: read as text it would sort after the ``3``.
    parsed = json.loads(encode_json({"name": 0, 3: 0, 1.5: 0, None: 0}))

    assert list(parsed) == ["1.5", "3", "null", "name"]


class _Grade(str, Enum):
    # Named against their values, so ordering by ``str()`` and by value differ.
    FIRST = "z"
    SECOND = "a"


@pytest.mark.parametrize(
    "payload",
    [
        {10.0: 1, 2.5: 2},
        {1: "a", 0.5: "b", 1.5: "c"},
        {_Grade.FIRST: 1, _Grade.SECOND: 2},
        {"rows": [{True: 1, 0: 2}], "b": (None,)},
    ],
    ids=["float-keys", "int-and-float-keys", "str-enum-keys", "nested"],
)
def test_encode_json_writes_what_sort_keys_writes_wherever_sort_keys_can(
    payload: dict[object, object],
) -> None:
    # Only a node mixing integer and string keys is ordered by hand; every
    # payload ``sort_keys`` can sort is written byte for byte as it writes it.
    expected = json.dumps(payload, indent=2, sort_keys=True, default=str, ensure_ascii=False)

    assert encode_json(payload) == expected


def test_a_mixed_payload_still_sorts_its_other_nodes_as_sort_keys_does() -> None:
    # The mixed ``items`` node sends the whole payload down the hand-ordered
    # path; ``grades`` beside it is still sorted by value, as ``sort_keys``
    # sorts it, not by the members' ``str()``.
    payload = {
        "items": {0: ["a"], "non_field_errors": ["b"]},
        "grades": {_Grade.FIRST: 1, _Grade.SECOND: 2},
    }

    assert list(json.loads(encode_json(payload))["grades"]) == ["a", "z"]


def test_a_payload_sort_keys_can_sort_is_not_walked_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Walking it in Python first would cap its nesting at the interpreter's
    # recursion limit rather than ``json.dumps``'s own.
    def _walked(payload: object) -> object:
        raise AssertionError("walked")

    # The package re-exports the function under the module's name.
    module = importlib.import_module("django_spec_mcp.output.encode_json")
    monkeypatch.setattr(module, "_reading_order", _walked)

    assert json.loads(encode_json({"b": {"c": [1]}, "a": 2})) == {"a": 2, "b": {"c": [1]}}


def test_encode_json_unicode_passthrough() -> None:
    out = encode_json({"name": "café"})
    assert "café" in out
