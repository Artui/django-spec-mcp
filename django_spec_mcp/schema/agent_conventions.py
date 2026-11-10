from __future__ import annotations

from django_service_specs import AudienceProjection, FieldAudience

HANDLE_DESCRIPTION = (
    "An opaque identifier. Pass it to other tools that ask for one; refer to the record "
    "by its name in anything you say, never by this value."
)
"""Fallback ``outputSchema`` wording for a handle that declares none of its own.

Per field, beside the field it describes, which is where a model reads it.
``HANDLE_LINE`` is the sentence that has nowhere else to go and rides the tool
description instead.

The Pydantic-AI toolset's sentence, word for word, so a spec served by both
transports describes its handles one way. It tells the model what to do instead
of reading the value out (name the record), where an instruction that only
forbids leaves it to guess."""

PAGED_QUERY_PARAM_SCOPE = (
    "On a paged result it applies to each item in `items`, never to the page "
    "envelope (`items`, `page`, `totalPages`, `hasNext`)."
)
"""What a read-shaping parameter applies to on a tool that returns a page.

The ``outputSchema`` of a paged tool describes the envelope, because that is what
the result is, and a field-selection param names fields. So the one shape a model
has been shown is exactly the wrong one to select against: ``{items{id, name}}``
reads naturally off the schema, and a presenter rendering one row at a time has
no ``items`` field. This sentence says which of the two shapes the param
addresses, beside the param (a kernel tool's ``shapes``, in
``kernel_tool_input_schema``) and again in the ``isError`` text
when a selection is refused while rendering
(``handlers.utils.rendering_refusal_result``), which is where a model that got
it wrong reads next.

Only a paged tool gets it. A service tool's result and an unpaginated list have
no envelope, so on those the sentence would describe a shape the model never
sees. Written here rather than supplied by the kernel for the reason the module
docstring gives: it is a prompt, and the wording belongs to the transport that
knows a model is reading."""

HANDLE_LINE = (
    "Fields described as opaque identifiers are for other tool calls, not for the "
    "reader: pass them to other tools that ask for one; refer to records by their "
    "name in anything you say, never by the identifier."
)
"""The line a tool's description gains when its output carries a handle.

Framed for a reader of one tool's description ("fields described as opaque
identifiers"), and carrying the advice the Pydantic-AI toolset's instructions
give, so both transports tell a model the same thing about the same field."""

MISSING_ARGUMENTS = "Missing required argument(s): {names}."
"""The message of a call refused for leaving out arguments its selectors need.

``{names}`` is the missing names, sorted, each in backticks, joined with
``", "``: the Pydantic-AI toolset's sentence for the same omission, so one call
reads the same on both transports."""


def append_agent_conventions(
    description: str | None,
    projection: AudienceProjection,
    *,
    handle_line: str | None = HANDLE_LINE,
) -> str | None:
    """Add the handle convention to a tool's description, when it has handles."""
    if handle_line is None:
        return description
    handles = [
        name
        for name, marking in projection.fields.items()
        if marking.audience is FieldAudience.HANDLE
    ]
    if not handles:
        return description
    line = handle_line
    if projection.label:
        line = f"Identify records by `{projection.label}`. {line}"
    return f"{description}\n\n{line}" if description else line


__all__ = ["append_agent_conventions"]
