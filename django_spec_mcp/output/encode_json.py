from __future__ import annotations

import json
from typing import Any


def _mixed_key(item: tuple[Any, Any]) -> tuple[int, Any, str]:
    """Sort numbers by value ahead of everything else, and the rest by name.

    On a node whose keys compare with each other this is the order ``sorted``
    gives, a ``str`` subclass such as a ``(str, Enum)`` member compared as the
    string it is rather than by its ``str()``.
    """
    key = item[0]
    if isinstance(key, int | float):
        return (0, key, "")
    return (1, 0, key if isinstance(key, str) else str(key))


def _reading_order(payload: Any) -> Any:
    """``payload`` with every dict's keys in order, a node mixing numbers and names included.

    ``sort_keys=True`` compares keys with each other, which it cannot do for a
    kernel ``many`` call's detail node when a list is refused as a whole beside
    its failing rows: the rows' integer indices sit with ``non_field_errors``
    (``test_a_many_call_refused_by_row_and_as_a_whole_is_a_validation_result``).
    Such a node puts its numbers first, by value, so rows 2 and 10 stay in that
    order rather than becoming ``"10"`` before ``"2"`` once written as strings.
    Every node whose keys compare is sorted as ``sort_keys`` sorts it.
    """
    if isinstance(payload, dict):
        return {
            key: _reading_order(value) for key, value in sorted(payload.items(), key=_mixed_key)
        }
    if isinstance(payload, list | tuple):
        return [_reading_order(item) for item in payload]
    return payload


def encode_json(payload: Any) -> str:
    """Encode ``payload`` as a stable, pretty JSON string.

    ``default=str`` renders DRF outputs containing ``Decimal``, ``UUID`` or
    ``datetime`` without raising; keys are sorted so the output is
    deterministic. A payload ``sort_keys`` can sort is written by it unchanged,
    so its bytes and its nesting limit are ``json.dumps``'s own
    (``test_encode_json_writes_what_sort_keys_writes_wherever_sort_keys_can``);
    only one it refuses is put in order first.
    """
    try:
        return json.dumps(payload, indent=2, sort_keys=True, default=str, ensure_ascii=False)
    except TypeError:
        return json.dumps(_reading_order(payload), indent=2, default=str, ensure_ascii=False)


__all__ = ["encode_json"]
