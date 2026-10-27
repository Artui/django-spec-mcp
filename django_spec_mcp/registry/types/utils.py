"""Shared validation and derivation for the tool and kernel resource bindings.

In a sibling ``utils.py`` rather than its own leaf module because it is
internal infrastructure for ``registry.types``, not part of the exported
type surface.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_service_specs import SelectorKind
from rest_framework_services import can_present_nothing as spec_can_present_nothing
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from django_spec_mcp.auth.permissions.scope_required import ScopeRequired
from django_spec_mcp.types.tool_content_kind import ToolContentKind

_MEDIA_KINDS: frozenset[ToolContentKind] = frozenset({ToolContentKind.IMAGE, ToolContentKind.AUDIO})


def validate_content_kind(
    *,
    name: str,
    content_kind: ToolContentKind,
    content_mime_type: str | None,
    include_structured_content: bool | None,
    include_output_schema: bool | None,
) -> None:
    """Refuse a content-kind declaration that cannot produce a valid result.

    Called from each binding's ``__post_init__``. Both checks are
    contradictions rather than judgement calls:

    - **Media needs a mime type.** ``mimeType`` is required on an ``image`` /
      ``audio`` block; without it the client holds a base64 string and no way
      to know what it decodes to.
    - **Media has no JSON projection.** ``structuredContent`` and
      ``outputSchema`` describe a JSON payload, and a tool returning a PNG has
      none. Both are suppressed for media kinds, so asking for either is a
      declaration that cannot be honoured.
    """
    if content_kind not in _MEDIA_KINDS:
        return
    if not content_mime_type:
        raise ImproperlyConfigured(
            f"Tool {name!r}: content_kind={content_kind.name} requires "
            "content_mime_type — the MCP spec makes mimeType mandatory on an "
            'image/audio block. Pass e.g. content_mime_type="image/png".'
        )
    if include_structured_content is True or include_output_schema is True:
        raise ImproperlyConfigured(
            f"Tool {name!r}: content_kind={content_kind.name} cannot be combined "
            "with include_structured_content=True or include_output_schema=True. "
            "Both describe a JSON result shape, and this tool returns binary "
            "media instead — there is nothing for them to describe."
        )


def rendered_kind(spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any]) -> SelectorKind:
    """Whether ``spec``'s rendered result is one object or a list of them.

    The cardinality drf-services' ``dispatch_spec`` gives the result, which is
    what the payload is rendered ``many=`` by:

    - A ``SelectorSpec`` answers its own ``kind``.
    - A ``many=True`` ``ServiceSpec`` answers ``LIST``. Its result is the list the
      service returns, rendered ``many``, and no re-fetch runs; its
      ``output_selector_spec`` is ``RETRIEVE`` by convention, because that kind
      describes one row of it. drf-services' own ``spec_to_json_schema`` answers
      the output phase the same way.
    - Any other ``ServiceSpec`` answers ``LIST`` when its ``output_selector_spec``
      declares ``LIST``, with a selector or without one: the re-fetch produces
      the set, and with no selector drf-services presents the service's own
      return as the set the declaration names (``kind="list"``), refusing a
      return that is not one. Anything else is a single object. The selector is
      not read: ``test_a_list_output_spec_with_no_selector_renders_and_advertises_a_list``
      fails for a service tool if it is. The guard is one
      ``and``-chain, so each conjunct is held by a row of
      ``test_the_rendered_kind_is_the_one_dispatch_gives``: ``no-output-spec``
      (``nested is not None``) and ``retrieve-output-no-re-read`` (the kind).

    Each binding's ``rendered_kind`` reads it, which picks the advertised
    ``outputSchema`` shape. Only a selector tool's schema was once kind-aware, so
    every other ``LIST`` output advertised one object while serving an array.
    """
    if isinstance(spec, SelectorSpec):
        return spec.kind
    if spec.many:
        return SelectorKind.LIST
    nested = spec.output_selector_spec
    if nested is not None and nested.kind is SelectorKind.LIST:
        return SelectorKind.LIST
    return SelectorKind.RETRIEVE


def can_present_nothing(spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any]) -> bool:
    """Whether a successful dispatch of ``spec`` can present nothing (``None``).

    drf-services' ``can_present_nothing`` answers, so this server's
    ``outputSchema`` admits ``{}`` exactly where drf-services' own output
    schema admits ``null``: a ``RETRIEVE``
    selector under ``allow_none``, a single-row service whose output re-read
    has a ``selector`` (dispatch materializes it with ``.first()``), and a
    single-row service presenting its own return that declares
    ``ServiceSpec(allow_none=True)``. A list result never does, empty at worst.

    A service returning ``None`` without the declaration is still served
    ``{}`` against a strict schema: drf-services presents an undeclared
    ``None`` rather than refusing it, and admitting ``{}`` for every such
    service would turn every row field optional for each client generating
    types from the schema. Held by
    ``test_a_service_declaring_allow_none_serves_an_empty_object_its_schema_admits``
    beside ``test_a_service_with_no_output_reread_keeps_its_schema_strict``.

    What stays this server's is what it does with the answer, and where it
    asks: the schema keeps its root an object and admits ``{}`` rather than
    ``null`` (``build_output_schema``).
    """
    return spec_can_present_nothing(spec)


def kernel_scope_gate(
    label: str, scopes: str | Sequence[str] | None
) -> tuple[tuple[str, ...] | None, tuple[Any, ...]]:
    """A kernel binding's ``scopes`` as it holds them, and the gate they build.

    Returns the scope names as a tuple, or ``None`` for no scope gate, and the
    transport checks the shared shell judges: one ``ScopeRequired`` naming every
    scope, or none. Run by each kernel binding's ``__post_init__``, so a binding
    built by hand gates as one the registrar built does.

    A bare string is the single-scope case, as ``ScopeRequired`` reads it:
    ``tuple("billing")`` would be seven one-character scopes no token carries
    (``test_a_handbuilt_kernel_resource_reads_a_bare_string_scope_as_one``). An
    empty sequence is refused, since it reads as a gate and admits every caller
    (``test_a_handbuilt_kernel_binding_refuses_an_empty_scope_tuple``). Each
    half of that guard is held by a test:
    ``test_a_kernel_resource_carries_its_scope_gate`` fails without the
    emptiness test, and
    ``test_a_handbuilt_kernel_resource_without_scopes_has_no_gate`` without the
    ``None`` one.
    """
    if isinstance(scopes, str):
        scopes = (scopes,)
    if scopes is not None and not scopes:
        raise ImproperlyConfigured(
            f"{label}: scopes=() permits every caller while reading as a gate. "
            "Name at least one scope, or leave scopes=None for no scope gate."
        )
    if scopes is None:
        return None, ()
    names: tuple[str, ...] = tuple(scopes)
    return names, (ScopeRequired(list(names)),)


__all__ = ["can_present_nothing", "kernel_scope_gate", "rendered_kind", "validate_content_kind"]
