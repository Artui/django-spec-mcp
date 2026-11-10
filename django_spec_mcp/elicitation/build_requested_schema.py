from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from django_spec_mcp.types.elicitation_scalar_types import ELICITATION_SCALAR_TYPES


def build_requested_schema(
    schema: Mapping[str, Any], *, admitted: Collection[str] | None = None
) -> dict[str, Any]:
    """Turn ``AdditionalInputRequired.schema`` into a spec-legal ``requestedSchema``."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, definition in schema.items():
        _reject_unusable(name, definition)
        if admitted is not None and name not in admitted:
            instead = (
                f"Ask for one of: {', '.join(sorted(admitted))}."
                if admitted
                else "This tool takes no argument from a client, so it can ask for nothing."
            )
            raise ImproperlyConfigured(
                f"AdditionalInputRequired asked for {name!r}, which this tool does not take "
                "from a client: the spec does not declare it, or the server fills it for the "
                f"call, so its answer would be refused, dropped or replaced. {instead}"
            )
        properties[name] = definition
        if "default" not in definition:
            required.append(name)

    built: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        built["required"] = required
    return built


def _reject_unusable(name: str, definition: Any) -> None:
    """Fail on anything outside ``PrimitiveSchemaDefinition``."""
    if not isinstance(definition, dict):
        raise ImproperlyConfigured(
            f"AdditionalInputRequired asked for {name!r} with {type(definition).__name__} "
            "instead of a schema object. Each value must be a JSON Schema fragment, "
            'e.g. {"type": "boolean"}.'
        )
    declared: Any = definition.get("type")
    if not isinstance(declared, str):
        declared = None
    if declared in ELICITATION_SCALAR_TYPES:
        return
    if declared == "array" and _is_multi_select(definition.get("items")):
        return
    raise ImproperlyConfigured(
        f"AdditionalInputRequired asked for {name!r} with type {declared!r}, which a client "
        "cannot render. An elicitation form takes top-level fields only: "
        f"{', '.join(sorted(ELICITATION_SCALAR_TYPES))}, or an array of enum values. "
        "Nested objects have to be flattened into separate fields."
    )


def _is_multi_select(items: Any) -> bool:
    """Whether ``items`` is the one array shape the spec allows: a list of enum values."""
    if not isinstance(items, dict):
        return False
    return isinstance(items.get("enum"), list) or isinstance(items.get("anyOf"), list)


__all__ = ["build_requested_schema"]
