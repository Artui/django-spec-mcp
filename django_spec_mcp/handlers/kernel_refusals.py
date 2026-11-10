from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from django_service_specs import (
    NON_FIELD_ERRORS,
    AdditionalInputRequired,
    DispatchError,
    InvalidArguments,
    NotPermitted,
    PrincipalUnavailable,
    ServiceError,
    ServiceValidationError,
)

from django_spec_mcp.handlers.input_dispatch import ask_for_input
from django_spec_mcp.handlers.types.kernel_refusal_scene import KernelRefusalScene
from django_spec_mcp.handlers.utils import (
    forbidden,
    missing_arguments_message,
    service_error_result,
    validation_error_data,
    validation_error_result,
)
from django_spec_mcp.output.error_tool_result import build_error_tool_result
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.tool_result import ToolResult

# What an answer returns: a tool result, the wire dict MRTR's ``input_required``
# already is, or a JSON-RPC error. The wire serialises the first; the in-process
# ``call_tool`` raises the refusal itself for the last.
KernelRefusalAnswer = Callable[
    [Any, KernelRefusalScene], ToolResult | dict[str, Any] | JsonRpcError
]


def _insufficient_permission(
    exc: NotPermitted | PrincipalUnavailable, scene: KernelRefusalScene
) -> JsonRpcError:
    """``-32006`` with no ``data``: a refusal from inside dispatch names no scope."""
    return forbidden([])


def _invalid_arguments(exc: InvalidArguments, scene: KernelRefusalScene) -> ToolResult:
    """The validation ``isError``, its ``detail`` the refusal's own per-field map."""
    missing = exc.missing
    if missing:
        message = missing_arguments_message(missing, scene.conventions)
        return _validation_result(exc.detail, scene, message=message)
    return _validation_result(exc.detail, scene)


def _dispatch_refused(exc: DispatchError, scene: KernelRefusalScene) -> ToolResult:
    """A bare ``DispatchError`` as the validation ``isError``."""
    return _validation_result({NON_FIELD_ERRORS: [str(exc)]}, scene)


def _service_validation(exc: ServiceValidationError, scene: KernelRefusalScene) -> ToolResult:
    """The validation ``isError`` a service's own refusal is served as."""
    return validation_error_result(exc, scene.arguments, config=scene.config)


def _validation_result(
    detail: Any, scene: KernelRefusalScene, *, message: str = "Invalid arguments"
) -> ToolResult:
    return build_error_tool_result(
        message,
        error_type="validation_error",
        detail=validation_error_data(
            detail, scene.arguments, include_value=scene.config.include_validation_value
        ),
    )


def _input_required(
    exc: AdditionalInputRequired, scene: KernelRefusalScene
) -> ToolResult | dict[str, Any] | JsonRpcError:
    """MRTR's ``input_required`` on the wire; a service error with no round left to ask in."""
    if scene.input_round is None:
        return service_error_result(exc)
    return ask_for_input(
        exc,
        scene.input_round.prior,
        scene.input_round.context,
        admitted=scene.input_round.admitted,
    )


def _service_error(exc: ServiceError, scene: KernelRefusalScene) -> ToolResult:
    """The service ``isError``, carrying an ``ActionUnavailable``'s ``code``."""
    if scene.span is not None and scene.config.record_service_exceptions:
        scene.span.record_exception(exc)
    return service_error_result(exc)


KERNEL_REFUSALS: Mapping[type[BaseException], KernelRefusalAnswer] = MappingProxyType(
    {
        # Read top to bottom, first ``isinstance`` match winning, so each
        # subclass's row sits above its base's. Each row is held by a test that
        # asserts its own answer and that it is not the answer of the row below
        # it, which is the answer the refusal falls to with its row deleted:
        # tests/handlers/test_kernel_refusals.py names one per row.
        NotPermitted: _insufficient_permission,
        PrincipalUnavailable: _insufficient_permission,
        InvalidArguments: _invalid_arguments,
        DispatchError: _dispatch_refused,
        # Ahead of ``ServiceError``, which would otherwise answer it.
        AdditionalInputRequired: _input_required,
        ServiceValidationError: _service_validation,
        # ``ServiceNotFound``, ``ServiceConflict`` and ``ActionUnavailable``
        # are answered here: ``service_error_result`` adds the ``code`` an
        # ``ActionUnavailable`` carries.
        ServiceError: _service_error,
    }
)
"""How the kernel path answers each refusal ``dispatch`` raises, in order.

``-32006`` for a denied or deactivated principal, the validation ``isError`` for
refused input, MRTR's ``input_required`` for a service asking for more, and the
service ``isError`` for any other ``ServiceError``. The kernel's
``unanswered_refusals`` holds the map total from the suite."""


__all__ = ["KERNEL_REFUSALS"]
