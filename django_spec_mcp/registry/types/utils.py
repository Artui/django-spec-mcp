"""Shared validation for the kernel tool and resource bindings.

In a sibling ``utils.py`` rather than its own leaf module because it is
internal infrastructure for ``registry.types``, not part of the exported
type surface.
"""

from __future__ import annotations

from collections.abc import Sequence

from django.core.exceptions import ImproperlyConfigured
from django_service_specs import PermissionCheck, check_permissions

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


def scope_names(label: str, scopes: str | Sequence[str] | None) -> tuple[str, ...] | None:
    """A binding's ``scopes`` as it holds them: a tuple of names, or ``None`` for no gate.

    Run by every binding's ``__post_init__``, so one built by hand gates as one
    the registrar built does. ``handlers.utils.scope_verdict`` judges the
    result against the caller's token.

    A bare string is the single-scope case: ``tuple("billing")`` would be seven
    one-character scopes no token carries
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
        return None
    return tuple(scopes)


def binding_checks(
    label: str, permissions: Sequence[PermissionCheck] | None
) -> tuple[PermissionCheck, ...]:
    """A prompt's or a view's ``permissions`` as it holds them, by the kernel's rule for a spec's.

    The kernel's ``check_permissions`` decides, so a prompt's or a view's
    checks are refused by exactly the rules a spec's ``permissions=`` gets: a
    string or any other non-sequence, a class where an instance belongs, an
    entry that is not a ``PermissionCheck``, an ``async def`` on any of the
    three check methods, and an empty sequence, which reads as a gate and
    admits everyone (``test_a_prompt_refuses_what_is_not_a_check``).

    Run at registration on the ``permissions=`` passed, so an explicit ``[]``
    is refused there, and by each binding's ``__post_init__`` on
    ``permissions or None``, so a binding built by hand is held to every other
    rule while its empty sequence, the field's default ``()``, is no checks.

    ``None`` is no checks and comes back as ``()``: unlike a spec's, these
    ``permissions`` are optional, and a prompt with neither them nor ``scopes``
    is what ``REQUIRE_TOOL_PERMISSIONS`` refuses.
    """
    return check_permissions(permissions, label=label) or ()


__all__ = ["binding_checks", "scope_names", "validate_content_kind"]
