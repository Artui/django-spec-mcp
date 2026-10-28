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
    missing_arguments_message,
    service_error_result,
    validation_error_data,
    validation_error_result,
)
from django_spec_mcp.output.error_tool_result import build_error_tool_result
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.tool_result import ToolResult
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode

# What an answer returns: a tool result, the wire dict MRTR's ``input_required``
# already is, or a JSON-RPC error. The wire serialises the first; the in-process
# ``call_tool`` raises the refusal itself for the last.
KernelRefusalAnswer = Callable[
    [Any, KernelRefusalScene], ToolResult | dict[str, Any] | JsonRpcError
]


def _insufficient_permission(
    exc: NotPermitted | PrincipalUnavailable, scene: KernelRefusalScene
) -> JsonRpcError:
    """``-32006`` with no ``data``: the scope gate's ``requiredScopes`` belong to
    the transport's own check, which answers before dispatch is reached, so a
    refusal from inside dispatch names no scope."""
    return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")


def _invalid_arguments(exc: InvalidArguments, scene: KernelRefusalScene) -> ToolResult:
    """The validation ``isError``, its ``detail`` the refusal's own per-field map.

    The shape ``validation_error_result`` serves. A refusal naming arguments
    the call left out is worded in the server's
    ``AgentConventions.missing_arguments``, from the kernel's own reading of the
    refusal (``InvalidArguments.missing``)
    (``test_a_missing_argument_is_worded_by_the_server``); any other refusal is
    ``Invalid arguments``. The detail is the same either way, every refused
    argument in it, so a call that left one argument out and sent another wrong
    names the missing one in the message and both in the detail."""
    missing = exc.missing
    if missing:
        message = missing_arguments_message(missing, scene.conventions)
        return _validation_result(exc.detail, scene, message=message)
    return _validation_result(exc.detail, scene)


def _dispatch_refused(exc: DispatchError, scene: KernelRefusalScene) -> ToolResult:
    """A bare ``DispatchError`` as the validation ``isError``.

    It carries a sentence and no per-field map, so the sentence becomes the
    non-field entry of ``detail``, which keeps the result's shape the one an
    ``InvalidArguments`` is served in."""
    return _validation_result({NON_FIELD_ERRORS: [str(exc)]}, scene)


def _service_validation(exc: ServiceValidationError, scene: KernelRefusalScene) -> ToolResult:
    """The validation ``isError`` a service's own refusal is served as, its
    message the service's own."""
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
    """MRTR's ``input_required`` on the wire; a service error where there is no
    further round to ask in, which is how the in-process ``call_tool`` answers
    it.

    One condition, the round being there or not: held both ways by
    ``test_additional_input_required_asks_for_input`` (a wire service tool,
    which asks) and by ``test_call_tool_answers_a_request_for_input_as_a_service_error``
    and ``test_a_kernel_selector_asking_for_input_is_answered_as_a_drfs_selector_is``
    (no round, so a service error)."""
    if scene.input_round is None:
        return service_error_result(exc)
    return ask_for_input(
        exc,
        scene.input_round.prior,
        scene.input_round.context,
        admitted=scene.input_round.admitted,
    )


def _service_error(exc: ServiceError, scene: KernelRefusalScene) -> ToolResult:
    """The service ``isError``, carrying an ``ActionUnavailable``'s ``code``.

    Recorded on the call's span under ``RECORD_SERVICE_EXCEPTIONS``. Each
    conjunct is held by a test: ``test_a_service_error_is_recorded_only_when_asked``
    fails without the setting's, and
    ``test_a_service_error_with_no_span_is_answered_without_one`` without the span's.
    """
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
