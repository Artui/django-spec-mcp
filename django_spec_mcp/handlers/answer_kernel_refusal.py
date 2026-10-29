from __future__ import annotations

from typing import Any

from django_service_specs import DispatchError, ServiceError

from django_spec_mcp.handlers.kernel_refusals import KERNEL_REFUSALS
from django_spec_mcp.handlers.types.kernel_refusal_scene import KernelRefusalScene
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.tool_result import ToolResult


def answer_kernel_refusal(
    exc: DispatchError | ServiceError, scene: KernelRefusalScene
) -> ToolResult | dict[str, Any] | JsonRpcError:
    """The answer the first row of ``KERNEL_REFUSALS`` that ``exc`` is an instance of gives.

    Walked by ``isinstance`` rather than looked up by ``type(exc)``, so a
    project's own subclass of a kernel refusal is answered as its base is. Both
    family bases have a row, so every refusal a caller catches is answered.
    """
    answer = next(answer for refusal, answer in KERNEL_REFUSALS.items() if isinstance(exc, refusal))
    return answer(exc, scene)


__all__ = ["answer_kernel_refusal"]
