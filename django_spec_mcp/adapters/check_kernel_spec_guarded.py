from __future__ import annotations

import warnings

from django.core.exceptions import ImproperlyConfigured
from django_service_specs import SelectorSpec, ServiceSpec

from django_spec_mcp.server.utils import UnguardedToolWarning


def check_kernel_spec_guarded(
    name: str, spec: ServiceSpec | SelectorSpec, *, require: bool, kind: str = "tool"
) -> bool:
    """Refuse, or warn about, a kernel spec declaring no permissions."""
    if spec.permissions is not None:
        return False
    problem = f"MCP {kind} {name!r}: its kernel spec declares no permissions (permissions=None)."
    if require:
        raise ImproperlyConfigured(
            f"{problem} Declare permissions on the spec, [Unrestricted()] for an "
            "operation open to every caller, or set "
            "SPEC_MCP['REQUIRE_TOOL_PERMISSIONS'] = False to call it without a "
            "permission check."
        )
    warnings.warn(
        f"{problem} It is called without a permission check, because "
        "SPEC_MCP['REQUIRE_TOOL_PERMISSIONS'] is False. Declare permissions on "
        "the spec, [Unrestricted()] for an operation open to every caller.",
        UnguardedToolWarning,
        stacklevel=4,
    )
    return True


__all__ = ["check_kernel_spec_guarded"]
