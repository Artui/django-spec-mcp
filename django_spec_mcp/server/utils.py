from __future__ import annotations

import warnings
from collections.abc import Iterable
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from django_spec_mcp.registry.resource_registry import ResourceRegistry
from django_spec_mcp.registry.types.ui_tool_meta import UIToolMeta
from django_spec_mcp.types.ui_meta_key import UI_META_KEY
from django_spec_mcp.types.ui_resource_mime_type import UI_RESOURCE_MIME_TYPE


class UnguardedToolWarning(UserWarning):
    """A tool, resource or prompt was registered with no MCP permissions at all."""


def check_tool_permissions_declared(
    name: str, *, guarded: bool, require: bool, kind: str = "tool"
) -> None:
    """Warn (or raise) when a registered binding carries neither permissions nor scopes."""
    if guarded:
        return
    problem = (
        f"MCP {kind} {name!r} is registered with no permissions and no scopes, so it "
        "answers any principal the transport authenticates. Pass "
        "permissions=[...] or scopes=[...] at registration."
    )
    if require:
        raise ImproperlyConfigured(
            f"{problem} To downgrade this to a warning while you migrate, set "
            "SPEC_MCP['REQUIRE_TOOL_PERMISSIONS'] = False."
        )
    warnings.warn(
        f"{problem} This is a warning because "
        "SPEC_MCP['REQUIRE_TOOL_PERMISSIONS'] is False; by default it is "
        "an error.",
        UnguardedToolWarning,
        stacklevel=3,
    )


class UndescribedToolWarning(UserWarning):
    """A tool was registered with no description."""


def check_tool_description_present(
    name: str, description: str | None, *, require: bool, stacklevel: int = 3
) -> None:
    """Warn (or raise) when a tool binding carries no description."""
    if description and description.strip():
        return
    message = (
        f"MCP tool {name!r} is registered with no description. `tools/list` will "
        "advertise it with an empty description, which is the only thing a model "
        "reads to decide whether and how to call it. Pass description='...' at "
        "registration, or set SPEC_MCP['REQUIRE_TOOL_DESCRIPTIONS'] = True "
        "to make this an error."
    )
    if require:
        raise ImproperlyConfigured(message)
    warnings.warn(message, UndescribedToolWarning, stacklevel=stacklevel)


class UnboundedListWarning(UserWarning):
    """A LIST selector tool was registered without pagination."""


def check_list_pagination_declared(
    name: str, *, paginate: bool, require: bool, stacklevel: int = 3
) -> None:
    """Warn (or raise) when a LIST selector tool has no pagination."""
    if paginate:
        return
    message = (
        f"MCP tool {name!r} is a LIST selector registered with paginate=False, so a "
        "call returns every row the selector resolves to. Unlike a paginated tool "
        "there is no honest way to clamp that at dispatch — the result carries no "
        "metadata that would tell the model rows were dropped — so an oversized "
        "result can only fail the call (see SPEC_MCP['MAX_PAGE_SIZE'], "
        "which bounds the row count, and ['MAX_RESULT_BYTES'], which bounds the "
        "encoded payload). "
        "Pass paginate=True, or set SPEC_MCP['REQUIRE_LIST_PAGINATION'] = "
        "True to make this an error."
    )
    if require:
        raise ImproperlyConfigured(message)
    warnings.warn(message, UnboundedListWarning, stacklevel=stacklevel)


def build_ui_tool_meta(
    *,
    name: str,
    ui: UIToolMeta | None,
    meta: dict[str, Any] | None,
    resources: ResourceRegistry,
    include_structured_content: bool | None,
    default_structured_content: bool,
) -> dict[str, Any]:
    """Validate a tool's view link and return its ``_meta`` contribution."""
    if ui is None:
        return {}
    if meta is not None and UI_META_KEY in meta:
        raise ValueError(
            f"Tool {name!r} got both ui= and a {UI_META_KEY!r} key in meta=. Both "
            "write the same _meta key, so one would silently win. Pass the typed "
            "ui= alone, or drop it and hand-write the whole bundle in meta=."
        )

    try:
        resolved = resources.resolve(ui.resource_uri)
    except ValueError as exc:
        raise ValueError(
            f"Tool {name!r} links to {ui.resource_uri!r}, which a registered "
            f"resource's URI template splits more than one way ({exc}). Name the "
            "view by its exact URI."
        ) from exc
    if resolved is None or resolved[0].mime_type != UI_RESOURCE_MIME_TYPE:
        raise ValueError(
            f"Tool {name!r} links to {ui.resource_uri!r}, which is not a view "
            "registered on this server. Register it with register_ui_resource() "
            "first — a host resolves the URI against this same server, so a link "
            "it cannot resolve renders nothing and reports nothing."
        )

    emits_structured = (
        include_structured_content
        if include_structured_content is not None
        else default_structured_content
    )
    if not emits_structured:
        raise ValueError(
            f"Tool {name!r} links to a view but does not emit structuredContent, "
            "which is the payload the view renders from — so the view would come "
            "up blank. Set include_structured_content=True on this registration"
            + (
                ""
                if include_structured_content is not None
                else " (it is off by default for this server — see "
                "SPEC_MCP['INCLUDE_STRUCTURED_CONTENT'])"
            )
            + ", or drop the ui= link."
        )

    return {UI_META_KEY: ui.to_dict()}


def check_completions_declared(
    label: str,
    completions: dict[str, Any],
    completable: Iterable[str],
) -> None:
    """Refuse a completer keyed to an argument that doesn't exist."""
    known: set[str] = set(completable)
    unknown: list[str] = sorted(set(completions) - known)
    if not unknown:
        return
    raise ImproperlyConfigured(
        f"{label}: completions name argument(s) {unknown!r} that this binding "
        f"does not have. Completable here: {sorted(known)!r}."
    )


__all__ = [
    "UnboundedListWarning",
    "UndescribedToolWarning",
    "UnguardedToolWarning",
    "build_ui_tool_meta",
    "check_completions_declared",
    "check_list_pagination_declared",
    "check_tool_description_present",
    "check_tool_permissions_declared",
]
