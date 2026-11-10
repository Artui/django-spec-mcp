from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from typing import Any

from django.template.loader import render_to_string
from django_service_specs import UNSET, PermissionCheck, UnsetType

from django_spec_mcp.adapters.utils import merge_meta
from django_spec_mcp.protocol.types.icon import Icon
from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.registry.types.ui_resource_meta import UIResourceMeta
from django_spec_mcp.registry.types.utils import scope_names
from django_spec_mcp.types.resource_encoding import ResourceEncoding
from django_spec_mcp.types.ui_meta_key import UI_META_KEY
from django_spec_mcp.types.ui_resource_mime_type import UI_RESOURCE_MIME_TYPE
from django_spec_mcp.ui.build_app_document import build_app_document


def ui_view_to_resource(
    *,
    name: str,
    uri: str,
    template_name: str | None = None,
    body_template_name: str | None = None,
    diagnostics: bool | None = None,
    html: str | None = None,
    render: Callable[[], str] | None = None,
    description: str | None = None,
    title: str | None = None,
    icons: tuple[Icon, ...] = (),
    cache_ttl_ms: int | UnsetType = UNSET,
    ui: UIResourceMeta | None = None,
    mime_type: str = UI_RESOURCE_MIME_TYPE,
    permissions: tuple[PermissionCheck, ...] = (),
    scopes: str | Sequence[str] | None = None,
    rate_limits: tuple[Any, ...] = (),
    annotations: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    always_listed: bool = False,
) -> ResourceBinding:
    """Lift an interactive HTML view into a resource binding."""
    sources = [s for s in (template_name, body_template_name, html, render) if s is not None]
    if len(sources) != 1:
        raise ValueError(
            f"register_ui_resource({name!r}) needs exactly one content source — "
            "body_template_name=, template_name=, html= or render=. "
            f"Got {len(sources)}."
        )

    if render is not None:
        _refuse_caller_aware_render(name, render)

    if ui is not None and meta is not None and UI_META_KEY in meta:
        raise ValueError(
            f"register_ui_resource({name!r}) got both ui= and a {UI_META_KEY!r} key in "
            "meta=. Both write the same _meta key, so one would silently win. Pass "
            "the typed ui= alone, or drop it and hand-write the whole bundle in meta=."
        )

    resolved: Callable[[], str]
    if template_name is not None:
        # Rendered per read, so a template edit shows without a restart.
        def resolved() -> str:
            return render_to_string(template_name)

    elif body_template_name is not None:
        # Rendered per read like a whole-document template, so an edit needs no restart.
        def resolved() -> str:
            return build_app_document(
                render_to_string(body_template_name),
                title=title or name,
                diagnostics=diagnostics,
            )

    elif html is not None:

        def resolved() -> str:
            return html

    else:
        resolved = render  # ty: ignore[invalid-assignment] - narrowed by the count check

    ui_meta: dict[str, Any] = {UI_META_KEY: ui.to_dict()} if ui is not None else {}

    return ResourceBinding(
        name=name,
        uri_template=uri,
        description=description,
        title=title,
        icons=icons,
        cache_ttl_ms=cache_ttl_ms,
        selector=resolved,
        mime_type=mime_type,
        encoding=ResourceEncoding.TEXT,
        permissions=permissions,
        scopes=scope_names(f"Resource {name!r}", scopes),
        rate_limits=rate_limits,
        annotations=annotations or {},
        meta=merge_meta(ui_meta, meta),
        always_listed=always_listed,
    )


__all__ = ["ui_view_to_resource"]


def _refuse_caller_aware_render(name: str, render: Callable[..., str]) -> None:
    """Refuse a view's render callable that would be handed the caller."""
    parameters = inspect.signature(render).parameters
    fillable: list[str] = [
        parameter_name
        for parameter_name, parameter in parameters.items()
        if parameter.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.VAR_KEYWORD,
        )
    ]
    if not fillable:
        return
    raise ValueError(
        f"register_ui_resource({name!r}) got a render callable taking {fillable!r}. "
        "A view's render callable must take no arguments: it is read through the same "
        "keyword pool as any other resource, which carries `request` and `user`, so a "
        "parameter here is handed the authenticated caller. Views skip the "
        "permissions-declared check precisely because they cannot read the caller, and "
        "hosts may cache one document across callers. Build the document from nothing "
        "and let the view hydrate itself from tool results, or register it with "
        "`register_resource` as a data resource, where the declaration check applies."
    )
