from __future__ import annotations

from collections.abc import Mapping

from django_spec_mcp.handlers.kernel_refusals import KERNEL_REFUSALS
from django_spec_mcp.handlers.types.exception_handler import ExceptionHandler
from django_spec_mcp.handlers.unmappable_exceptions import UNMAPPABLE_EXCEPTIONS


def exception_handler_for(
    exc: Exception, exception_map: Mapping[type[Exception], ExceptionHandler]
) -> ExceptionHandler | None:
    """The handler ``exception_map`` holds for ``exc``, the most specific type winning.

    Walked along the exception's MRO rather than the mapping's order, so a
    subclass and its base can both be mapped in any order
    (``test_the_most_specific_type_wins``).

    The kernel's own rows take part in the same walk, and a row met first ends it:
    the more specific answer is the row's, so mapping ``Exception`` or
    ``ServiceError`` reaches what no row answers more closely and leaves a request
    for input or a refused argument as it is
    (``test_a_broad_entry_leaves_a_request_for_input_alone``). An entry at or
    below a row wins over it, which is how a project's own ``ServiceError`` subclass
    reaches its handler (``test_a_project_subclass_of_a_kernel_refusal_reaches_its_handler``).

    What ``UNMAPPABLE_EXCEPTIONS`` holds is never looked up, ahead of the walk: Django's
    ``PermissionDenied`` has no row to stop at, and a class that is a denial and a
    project's exception too meets its project base first
    (``test_no_entry_reaches_a_denial_or_a_configuration_error``).
    """
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
