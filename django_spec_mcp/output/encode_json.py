from __future__ import annotations

import json
from typing import Any


def _reading_order(payload: Any) -> Any:
    """``payload`` with every dict's keys in order: integers by value, then the rest by name.

    ``sort_keys=True`` compares keys with each other, which it cannot do for a
    kernel ``many`` call's detail node when a list is refused as a whole beside
    its failing rows: the rows' integer indices sit with ``non_field_errors``
    (``test_a_many_call_refused_by_row_and_as_a_whole_is_a_validation_result``).
    Integers sort ahead as rows, so rows 2 and 10 stay in that order rather than
    becoming ``"10"`` before ``"2"`` once written as strings. Keys of one kind
    sort as ``sort_keys`` sorts them.
    """
    if isinstance(payload, dict):
        return {
            key: _reading_order(value)
            for key, value in sorted(
                payload.items(),
                key=lambda item: (
                    (0, item[0], "") if isinstance(item[0], int) else (1, 0, str(item[0]))
                ),
            )
        }
    if isinstance(payload, list | tuple):
        return [_reading_order(item) for item in payload]
    return payload


def encode_json(payload: Any) -> str:
    """Encode ``payload`` as a stable, pretty JSON string.

    ``default=str`` renders DRF outputs containing ``Decimal``, ``UUID`` or
    ``datetime`` without raising; keys are sorted so the output is
    deterministic, a node mixing integer and string keys included.
    """
    return json.dumps(_reading_order(payload), indent=2, default=str, ensure_ascii=False)


__all__ = ["encode_json"]
