from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_TEMPLATE_VAR = re.compile(r"\{([^}]+)\}")


def render_invalidations(
    templates: tuple[str, ...],
    *,
    payload: Any,
    arguments: Mapping[str, Any],
) -> tuple[str, ...]:
    """Turn a binding's ``invalidates=`` templates into the URIs that changed."""
    values: dict[str, Any] = {**dict(arguments), **_result_keys(payload)}
    rendered: list[str] = []
    for template in templates:
        uri: str | None = _render(template, values)
        if uri is not None:
            rendered.append(uri)
    return tuple(dict.fromkeys(rendered))


def _render(template: str, values: Mapping[str, Any]) -> str | None:
    out: list[str] = []
    last: int = 0
    for match in _TEMPLATE_VAR.finditer(template):
        # A present-but-null key is as unusable as a missing one.
        value: Any = values.get(match.group(1))
        if value is None:
            return None
        out.append(template[last : match.start()])
        out.append(str(value))
        last = match.end()
    out.append(template[last:])
    return "".join(out)


def _result_keys(payload: Any) -> dict[str, Any]:
    """The rendered tool result's fields, if it has any addressable ones."""
    if not isinstance(payload, dict):
        return {}
    structured: Any = payload.get("structuredContent")
    return dict(structured) if isinstance(structured, dict) else {}


__all__ = ["render_invalidations"]
