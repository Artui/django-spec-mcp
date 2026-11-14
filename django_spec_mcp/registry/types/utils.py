from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    FilePresenter,
    PermissionCheck,
    SelectorSpec,
    UnsetType,
    check_permissions,
    file_output,
)

from django_spec_mcp.types.file_content_kinds import FILE_CONTENT_KINDS
from django_spec_mcp.types.resource_encoding import ResourceEncoding
from django_spec_mcp.types.tool_content_kind import ToolContentKind


def validate_content_kind(
    *,
    name: str,
    content_kind: ToolContentKind,
    content_mime_type: str | None,
    include_structured_content: bool | None,
    include_output_schema: bool | None,
) -> None:
    """Refuse a content-kind declaration that cannot produce a valid result."""
    if content_kind not in FILE_CONTENT_KINDS:
        return
    if not content_mime_type:
        raise ImproperlyConfigured(
            f"Tool {name!r}: content_kind={content_kind.name} requires "
            "content_mime_type, which MCP makes mandatory on a media block and a "
            "file's blob. Declare the file on the spec with FilePresenter(content_type)."
        )
    if include_structured_content is True or include_output_schema is True:
        raise ImproperlyConfigured(
            f"Tool {name!r}: content_kind={content_kind.name} cannot be combined "
            "with include_structured_content=True or include_output_schema=True. "
            "Both describe a JSON result shape, and this tool returns a file "
            "instead, so there is nothing for them to describe."
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


def file_presenter(label: str, spec: Any) -> FilePresenter | None:
    """The kernel's ``file_output(spec)``, its refusal named by ``label``."""
    try:
        return file_output(spec)
    except ImproperlyConfigured as exc:
        raise ImproperlyConfigured(f"{label}: {exc}") from exc


def file_content_kind(content_type: str) -> ToolContentKind:
    """The kind a file of ``content_type`` takes on a tool, by its top-level type."""
    top = content_type.strip().lower().split("/", 1)[0]
    if top == "image":
        return ToolContentKind.IMAGE
    if top == "audio":
        return ToolContentKind.AUDIO
    return ToolContentKind.RESOURCE


def derived_tool_content(
    label: str,
    spec: Any,
    content_kind: ToolContentKind | UnsetType,
    content_mime_type: str | None | UnsetType,
) -> tuple[ToolContentKind, str | None]:
    """A tool's content kind and mime type, each one not given taken from its spec's file."""
    presenter = file_presenter(label, spec)
    kind = ToolContentKind.TEXT if presenter is None else file_content_kind(presenter.content_type)
    mime_type = None if presenter is None else presenter.content_type
    return (
        kind if isinstance(content_kind, UnsetType) else content_kind,
        mime_type if isinstance(content_mime_type, UnsetType) else content_mime_type,
    )


def derived_resource_body(
    label: str,
    spec: Any,
    encoding: ResourceEncoding | UnsetType,
    mime_type: str | UnsetType,
) -> tuple[ResourceEncoding, str]:
    """A resource's encoding and mime type, each one not given taken from its spec's file."""
    presenter = file_presenter(label, spec)
    if presenter is None:
        derived = (ResourceEncoding.JSON, "application/json")
    else:
        derived = (ResourceEncoding.BLOB, presenter.content_type)
    return (
        derived[0] if isinstance(encoding, UnsetType) else encoding,
        derived[1] if isinstance(mime_type, UnsetType) else mime_type,
    )


def check_tool_file(
    label: str, spec: Any, content_kind: ToolContentKind, content_mime_type: str | None
) -> None:
    """Refuse a tool whose content kind or mime type disagrees with its spec's file."""
    presenter = file_presenter(label, spec)
    if presenter is None:
        if content_kind in FILE_CONTENT_KINDS:
            raise ImproperlyConfigured(
                f"{label}: content_kind={content_kind.name} serves a file, and its spec "
                "presents none. A file is declared on the spec: present it through "
                "FilePresenter(content_type), and the tool takes its kind from the type."
            )
        return
    content_type = presenter.content_type
    kind = file_content_kind(content_type)
    if kind is ToolContentKind.RESOURCE and isinstance(spec, SelectorSpec):
        raise ImproperlyConfigured(
            f"{label}: its SelectorSpec presents a {content_type!r} file, which is neither "
            "image/* nor audio/*. A row's file is a resource: register the spec with "
            "register_resource, which serves it as a blob."
        )
    if content_kind is not kind:
        raise ImproperlyConfigured(
            f"{label}: content_kind={content_kind.name} disagrees with its spec's "
            f"FilePresenter({content_type!r}), which a tool serves as {kind.name}. Leave "
            "content_kind out, and it is taken from the presenter."
        )
    if content_mime_type != content_type:
        raise ImproperlyConfigured(
            f"{label}: content_mime_type={content_mime_type!r} disagrees with its spec's "
            f"FilePresenter({content_type!r}). Leave content_mime_type out, and it is "
            "taken from the presenter."
        )


def check_resource_file(label: str, spec: Any, encoding: ResourceEncoding, mime_type: str) -> None:
    """Refuse a resource whose encoding or mime type disagrees with its spec's file."""
    presenter = file_presenter(label, spec)
    if presenter is None:
        if encoding is ResourceEncoding.BLOB:
            raise ImproperlyConfigured(
                f"{label}: encoding=BLOB serves a file, and its spec presents none. A file "
                "is declared on the spec: present it through FilePresenter(content_type), "
                "and the resource is a blob of that type."
            )
        return
    content_type = presenter.content_type
    if encoding is not ResourceEncoding.BLOB:
        raise ImproperlyConfigured(
            f"{label}: encoding={encoding.name} disagrees with its spec's "
            f"FilePresenter({content_type!r}), which a resource serves as BLOB. Leave "
            "encoding out, and it is taken from the presenter."
        )
    if mime_type != content_type:
        raise ImproperlyConfigured(
            f"{label}: mime_type={mime_type!r} disagrees with its spec's "
            f"FilePresenter({content_type!r}). Leave mime_type out, and it is taken from "
            "the presenter."
        )


__all__ = [
    "binding_checks",
    "check_resource_file",
    "check_tool_file",
    "derived_resource_body",
    "derived_tool_content",
    "file_content_kind",
    "file_presenter",
    "scope_names",
    "validate_content_kind",
]
