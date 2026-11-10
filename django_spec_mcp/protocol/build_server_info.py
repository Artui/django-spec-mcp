from __future__ import annotations

from typing import Any

from django_spec_mcp.conf import get_setting
from django_spec_mcp.protocol.types.icon import Icon
from django_spec_mcp.protocol.types.implementation import Implementation
from django_spec_mcp.types.icon_theme import IconTheme
from django_spec_mcp.version import __version__ as package_version


def build_server_info(
    name: str | None = None,
    version: str | None = None,
    title: str | None = None,
    website_url: str | None = None,
    icons: tuple[Icon, ...] | None = None,
) -> Implementation:
    """Resolve a server's wire identity, falling back to the ``SERVER_INFO`` setting."""
    server_info_settings: dict[str, Any] = get_setting("SERVER_INFO")
    return Implementation(
        name=name if name is not None else server_info_settings.get("name", "django-spec-mcp"),
        version=version
        if version is not None
        else server_info_settings.get("version", package_version),
        title=title if title is not None else server_info_settings.get("title"),
        description=server_info_settings.get("description"),
        website_url=website_url
        if website_url is not None
        else server_info_settings.get("websiteUrl"),
        icons=icons if icons is not None else _coerce_icons(server_info_settings.get("icons")),
    )


def _coerce_icons(raw: Any) -> tuple[Icon, ...]:
    """Normalise the ``SERVER_INFO`` representation of ``icons``."""
    if not raw:
        return ()
    return tuple(item if isinstance(item, Icon) else _icon_from_mapping(item) for item in raw)


def _icon_from_mapping(item: Any) -> Icon:
    """Build an icon from settings data, which uses the wire spellings."""
    theme: Any = item.get("theme")
    return Icon(
        src=item["src"],
        mime_type=item.get("mimeType"),
        sizes=tuple(item.get("sizes", ())),
        theme=IconTheme(theme) if theme else None,
    )


__all__ = ["build_server_info"]
