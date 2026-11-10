from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django_spec_mcp.types.ui_visibility import UIVisibility


@dataclass(frozen=True)
class UIToolMeta:
    """Links a tool to the interactive view that renders its result."""

    resource_uri: str
    visibility: Sequence[UIVisibility] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the extension's camelCase wire shape, omitting empties."""
        out: dict[str, Any] = {"resourceUri": self.resource_uri}
        if self.visibility:
            out["visibility"] = [v.value for v in self.visibility]
        return out


__all__ = ["UIToolMeta"]
