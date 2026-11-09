"""The Validator ``invoices.write_off`` takes, which declares itself per transport."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django_service_specs import (
    Parameter,
    Parameters,
    ValidationContext,
    Validator,
    call_transport,
)

from django_spec_mcp import MCP_TRANSPORT


class WriteOffValidator(Validator):
    """``number`` from anyone, and ``reason`` from a caller over MCP.

    A model has no one standing behind it to ask afterwards, so a call over MCP
    says why; a transport with a person at it would not be asked. ``tools/list``
    reads this under the ``mcp`` stamp, so ``reason`` is listed and required there,
    and a client following the schema sends it. The stamp names a transport, never
    a caller: its ``request`` is ``None`` on a listing, so read ``call_transport``
    here and not the request.
    """

    def parameters(self, *, call_context: dict[str, Any] | None = None) -> Parameters:
        declared = [Parameter("number", "string", required=True)]
        if call_context is not None and call_transport(call_context) == MCP_TRANSPORT:
            declared.append(Parameter("reason", "string", required=True))
        return Parameters(declared)

    def validate(self, arguments: Mapping[str, Any], context: ValidationContext) -> dict[str, Any]:
        return dict(arguments)
