from __future__ import annotations

import string
from collections.abc import Mapping
from dataclasses import dataclass, fields
from types import MappingProxyType

from django.core.exceptions import ImproperlyConfigured

from django_spec_mcp.schema.agent_conventions import (
    HANDLE_DESCRIPTION,
    HANDLE_LINE,
    MISSING_ARGUMENTS,
    PAGED_QUERY_PARAM_SCOPE,
)


@dataclass(frozen=True)
class AgentConventions:
    """The sentences this server writes for a model, one field per sentence."""

    handle_field_description: str | None = HANDLE_DESCRIPTION
    """The ``outputSchema`` description of a handle field that declares none of
    its own, beside the field in every tool's output schema. ``None`` leaves
    such a field undescribed. A ``FieldMarking.handle("...")`` keeps its own
    words whatever this says. No placeholders."""

    handle_line: str | None = HANDLE_LINE
    """The line appended to the description of a tool whose output carries a
    handle, after ``Identify records by `<label>`.`` when the presenter
    declares a label. ``None`` drops the line and that prefix with it. No
    placeholders."""

    query_param_on_pages: str | None = PAGED_QUERY_PARAM_SCOPE
    """What a read-shaping parameter, one of a spec's ``shapes``, applies to on
    a tool that returns a page. Appended to the param's description in the tool's ``inputSchema``, and
    to the ``isError`` message when a value it supplied is refused while that
    tool's result is rendered. ``None`` drops it from both. No placeholders."""

    missing_arguments: str = MISSING_ARGUMENTS
    """The message of a ``validation_error`` result for a call that left out
    arguments the tool's selectors cannot run without: a selector parameter
    with no default or a service tool's target lookup. ``{names}`` is the missing names, sorted, each
    in backticks, joined with ``", "``; the ``detail`` stays keyed by each name.
    A validator's own refusal, a missing field included, keeps
    ``"Invalid arguments"``. Never ``None``, empty or only whitespace: it is
    the result's whole message."""

    def __post_init__(self) -> None:
        # Checked here, at construction, so a typo fails when the server is built.
        for field in fields(self):
            _validate(field.name, getattr(self, field.name))


_PLACEHOLDERS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "handle_field_description": frozenset(),
        "handle_line": frozenset(),
        "query_param_on_pages": frozenset(),
        "missing_arguments": frozenset({"names"}),
    }
)
"""The placeholders each field accepts.

Keyed by every field, so a field added without an entry fails every
construction (``test_every_field_declares_its_placeholders``) rather than
accepting anything."""

_SAMPLES: Mapping[str, str] = MappingProxyType({"names": "`name`"})
"""A value of the type each placeholder is rendered with, for the trial render:
``{names}`` receives the missing names already joined into one string."""

_NEVER_NONE = frozenset({"missing_arguments"})
"""The fields that are the whole message of what they word, so can be neither
dropped nor left blank: an empty string would send the model exactly the empty
message the ``None`` refusal exists to stop."""


def _validate(name: str, value: object) -> None:
    """Refuse a field the server could not render, naming it."""
    if value is None:
        if name in _NEVER_NONE:
            raise ImproperlyConfigured(
                f"AgentConventions.{name} cannot be None: it is the whole message of a "
                "result, and a result with nothing in it tells the model nothing."
            )
        return
    if not isinstance(value, str):
        expected = "a string" if name in _NEVER_NONE else "a string or None"
        raise ImproperlyConfigured(
            f"AgentConventions.{name} must be {expected}, not {type(value).__name__}."
        )
    if name in _NEVER_NONE and not value.strip():
        raise ImproperlyConfigured(
            f"AgentConventions.{name} cannot be empty or only whitespace: it is the whole "
            "message of a result, and a result with nothing in it tells the model nothing."
        )
    accepted = _PLACEHOLDERS[name]
    try:
        used = {field for _, field, _, _ in string.Formatter().parse(value) if field is not None}
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"AgentConventions.{name} is not a valid format string ({exc}); write a literal "
            "brace twice, as {{ or }}."
        ) from exc
    unknown = sorted(used - accepted)
    if unknown:
        listed = ", ".join(f"{{{placeholder}}}" for placeholder in unknown)
        allowed = ", ".join(f"{{{placeholder}}}" for placeholder in sorted(accepted)) or "none"
        raise ImproperlyConfigured(
            f"AgentConventions.{name} uses {listed}, which it does not accept (accepted: "
            f"{allowed}); write a literal brace twice, as {{{{ or }}}}."
        )
    try:
        value.format(**{placeholder: _SAMPLES[placeholder] for placeholder in accepted})
    except Exception as exc:
        raise ImproperlyConfigured(f"AgentConventions.{name} cannot be rendered: {exc!r}.") from exc


__all__ = ["AgentConventions"]
