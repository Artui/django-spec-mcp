from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured

from django_spec_mcp.types.icon_theme import IconTheme

_ALLOWED_SCHEMES: frozenset[str] = frozenset({"https", "data"})


@dataclass(frozen=True)
class Icon:
    """One entry in a wire type's ``icons`` array."""

    src: str
    mime_type: str | None = None
    sizes: tuple[str, ...] = ()
    theme: IconTheme | None = None

    def __post_init__(self) -> None:
        scheme: str = urlparse(self.src).scheme.lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise ImproperlyConfigured(
                f"Icon src={self.src!r} uses the {scheme or 'relative'!r} scheme. "
                "MCP clients are required to reject icon URIs that are not "
                "https: or data:, so this icon would never be displayed. Serve "
                "the image over HTTPS, or inline it as a data: URI."
            )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"src": self.src}
        if self.mime_type is not None:
            out["mimeType"] = self.mime_type
        if self.sizes:
            out["sizes"] = list(self.sizes)
        if self.theme is not None:
            out["theme"] = self.theme.value
        return out


__all__ = ["Icon"]
