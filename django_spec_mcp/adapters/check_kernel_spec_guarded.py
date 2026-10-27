"""``REQUIRE_TOOL_PERMISSIONS`` for a kernel spec, which declares its own guard."""

from __future__ import annotations

import warnings

from django.core.exceptions import ImproperlyConfigured
from django_service_specs import SelectorSpec, ServiceSpec

from django_spec_mcp.server.utils import UnguardedToolWarning


def check_kernel_spec_guarded(
    name: str, spec: ServiceSpec | SelectorSpec, *, require: bool, kind: str = "tool"
) -> None:
    """Refuse, or warn about, a kernel spec declaring no permissions.

    A kernel spec's guard is its own ``permissions``; ``scopes=`` is a transport
    gate and is not counted. ``None`` is the only unguarded declaration, since
    the kernel refuses an empty sequence when the spec is built. Under
    ``REQUIRE_TOOL_PERMISSIONS`` it is refused here. With the setting off it
    registers with an ``UnguardedToolWarning``, emitted once, at registration,
    and each call runs under a kernel ``Grant`` minted for that spec and the
    calling principal (``kernel_utils.unguarded_grant``), so the kernel's
    dispatch, which refuses an unguarded spec on its own, runs it unchecked
    (``test_an_unguarded_kernel_tool_is_called_with_the_setting_off``).
    """
    if spec.permissions is not None:
        return
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


__all__ = ["check_kernel_spec_guarded"]
