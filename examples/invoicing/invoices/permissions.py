"""``OpenPrompt`` - the gate on this example's prompt, which lets every caller through."""

from __future__ import annotations

from django.http import HttpRequest

from django_spec_mcp import TokenInfo


class OpenPrompt:
    """An ``MCPPermission`` that admits every caller, saying so out loud.

    A prompt is a render callable rather than a spec, so it is gated by the
    server's own ``MCPPermission`` objects (``ScopeRequired``,
    ``DjangoPermRequired`` or one of your own) rather than by a spec's
    ``permissions``. A prompt with none is refused, as an unguarded tool is;
    this one is deliberately open, the prompt's counterpart of the specs'
    ``Unrestricted()``. Swap it for the real gate in anything real.
    """

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        return True
