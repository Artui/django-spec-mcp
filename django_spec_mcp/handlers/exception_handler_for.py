from __future__ import annotations

from collections.abc import Mapping

from django_spec_mcp.handlers.kernel_refusals import KERNEL_REFUSALS
from django_spec_mcp.handlers.types.exception_handler import ExceptionHandler
from django_spec_mcp.handlers.unmappable_exceptions import UNMAPPABLE_EXCEPTIONS


def exception_handler_for(
    exc: Exception, exception_map: Mapping[type[Exception], ExceptionHandler]
) -> ExceptionHandler | None:
    """The handler ``exception_map`` holds for ``exc``, the most specific type winning."""
    if isinstance(exc, UNMAPPABLE_EXCEPTIONS):
        return None
    for klass in type(exc).__mro__:
        handler = exception_map.get(klass)
        if handler is not None:
            return handler
        if klass in KERNEL_REFUSALS:
            return None
    return None


__all__ = ["exception_handler_for"]
