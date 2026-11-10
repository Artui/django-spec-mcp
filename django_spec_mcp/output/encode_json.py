from __future__ import annotations

import json
from typing import Any


def _mixed_key(item: tuple[Any, Any]) -> tuple[int, Any, str]:
    """Sort numbers by value ahead of everything else, and the rest by name."""
    key = item[0]
    if isinstance(key, int | float):
        return (0, key, "")
    return (1, 0, key if isinstance(key, str) else str(key))


def _reading_order(payload: Any) -> Any:
    """``payload`` with every dict's keys in order, a node mixing numbers and names included."""
    if isinstance(payload, dict):
        return {
            key: _reading_order(value) for key, value in sorted(payload.items(), key=_mixed_key)
        }
    if isinstance(payload, list | tuple):
        return [_reading_order(item) for item in payload]
    return payload


def encode_json(payload: Any) -> str:
    """Encode ``payload`` as a stable, pretty JSON string."""
    try:
        return json.dumps(payload, indent=2, sort_keys=True, default=str, ensure_ascii=False)
    except TypeError:
        return json.dumps(_reading_order(payload), indent=2, default=str, ensure_ascii=False)


__all__ = ["encode_json"]
