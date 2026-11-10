from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django_spec_mcp.registry.types.ui_csp import UICsp
from django_spec_mcp.types.ui_permission import UIPermission


@dataclass(frozen=True)
class UIResourceMeta:
    """What a host needs to know to render an interactive view."""

    csp: UICsp | None = None
    permissions: Sequence[UIPermission] = ()
    domain: str | None = None
    prefers_border: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the extension's camelCase wire shape, omitting empties."""
        out: dict[str, Any] = {}
        if self.csp is not None:
            csp = self.csp.to_dict()
            if csp:
                out["csp"] = csp
        if self.permissions:
            out["permissions"] = [p.value for p in self.permissions]
        if self.domain is not None:
            out["domain"] = self.domain
        if self.prefers_border is not None:
            out["prefersBorder"] = self.prefers_border
        return out


__all__ = ["UIResourceMeta"]
