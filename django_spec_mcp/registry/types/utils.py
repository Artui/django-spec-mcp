"""Shared validation for the kernel tool and resource bindings.

In a sibling ``utils.py`` rather than its own leaf module because it is
internal infrastructure for ``registry.types``, not part of the exported
type surface.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.core.exceptions import ImproperlyConfigured

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


__all__ = ["kernel_scope_gate", "validate_content_kind"]
