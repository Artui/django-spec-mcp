from __future__ import annotations

import importlib
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any


# Resolved via ``importlib``: OpenTelemetry is an optional extra the package must run without.
def _resolve_otel_trace() -> Any:
    try:
        return importlib.import_module("opentelemetry.trace")
    except ImportError:  # pragma: no cover - exercised by the no-extras smoke job
        return None


_otel_trace: Any = _resolve_otel_trace()

# Follows the package's import path so OTel collectors group spans coherently.
_TRACER_NAME: str = "django_spec_mcp"


class _NoopSpan:
    """Fallback span used when ``opentelemetry`` is not installed."""

    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def set_status(self, status: Any) -> None:
        return None

    def record_exception(self, exc: BaseException) -> None:
        return None


@contextmanager
def span(name: str, attributes: dict[str, Any] | None = None) -> Generator[Any]:
    """Start an MCP span; no-op if OpenTelemetry isn't installed."""
    if _otel_trace is None:
        yield _NoopSpan()
        return
    tracer = _otel_trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(name, attributes=attributes or {}) as otel_span:
        yield otel_span


__all__ = ["span"]
