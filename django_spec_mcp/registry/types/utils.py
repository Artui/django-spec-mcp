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
    """Refuse a content-kind declaration that cannot produce a valid result."""
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
    """A binding's ``scopes`` as it holds them: a tuple of names, or ``None`` for no gate."""
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
    """A prompt's or a view's ``permissions`` as it holds them."""
    return check_permissions(permissions, label=label) or ()


__all__ = ["binding_checks", "scope_names", "validate_content_kind"]
