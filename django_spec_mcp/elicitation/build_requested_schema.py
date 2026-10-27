from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from django_spec_mcp.types.elicitation_scalar_types import ELICITATION_SCALAR_TYPES


def build_requested_schema(
    schema: Mapping[str, Any], *, admitted: Collection[str] | None = None
) -> dict[str, Any]:
    """Turn ``AdditionalInputRequired.schema`` into a spec-legal ``requestedSchema``.

    The service declares only the properties — ``{"confirmed": {"type":
    "boolean"}}`` — because that is all a transport-neutral exception can
    usefully say. The object wrapper and the ``required`` list are MCP's, so
    they are added here.

    **Required is inferred from ``default``.** A property that declares one has
    said what to do without an answer; one that has not is something the service
    is waiting for.

    **Raises rather than shipping a schema the client must refuse.** The subset
    MCP allows is narrow — *"only top-level properties, without nesting"*,
    primitives and enums — and a client receiving anything else may reject the
    whole result. An unusable schema is a programming error in the service, so
    it fails loudly here, where its author can still be told which property is
    wrong.

    **``admitted`` is the argument names the tool takes from a client**, and the
    kernel tool path (``run_kernel_tool``, sync and async) always passes it: the
    spec's parameters less the names the server fills for the call, the binding's
    ``server_arguments`` and the kernel's ``server_owned_keys`` (``admitted_arguments``).
    The answer comes back as an ordinary argument, so a property outside that set is
    one the call would then refuse as unknown, drop under ``IGNORE`` or replace with
    the server's value, after the user has filled it in; it is refused here instead,
    as an unrenderable one is, and the ``ImproperlyConfigured`` becomes a ``-32603``
    naming the property and the names that are admitted. ``None`` asks nothing of the
    names, for a caller that does not know the set: a drf-services tool.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, definition in schema.items():
        _reject_unusable(name, definition)
        # Two conjuncts, one branch arc: ``admitted is not None`` is held by
        # ``test_no_admitted_set_judges_the_schema_alone`` and the membership by
        # ``test_a_property_the_tool_admits_passes``.
        if admitted is not None and name not in admitted:
            # Reaches the client verbatim as a ``-32603``, so it holds for both ways
            # of not being admitted: undeclared, and declared but filled by the server.
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
        # Omitted rather than sent empty: ``required: []`` and no ``required``
        # mean the same thing, and the spec's own examples show the shorter one.
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
    # ``str`` first: JSON Schema lets ``type`` be a *list*, which an author might
    # reasonably write and which raises ``TypeError: unhashable`` on the
    # membership test below — a crash where a refusal was wanted.
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
    """Whether ``items`` is the one array shape the spec allows — a list of enum
    values, titled (``anyOf``) or not (``enum``)."""
    if not isinstance(items, dict):
        return False
    return isinstance(items.get("enum"), list) or isinstance(items.get("anyOf"), list)


__all__ = ["build_requested_schema"]
