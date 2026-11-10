from __future__ import annotations

from typing import Any

from django_spec_mcp.observability import get_logger
from django_spec_mcp.output.encode_json import encode_json

logger = get_logger(__name__)


def enforce_result_bytes(payload: Any, max_bytes: int | None, *, label: str) -> str | None:
    """Return an explanatory message when ``payload`` exceeds ``max_bytes``, else ``None``."""
    if max_bytes is None:
        return None
    size: int = len(encode_json(payload).encode("utf-8"))
    if size <= max_bytes:
        return None
    logger.warning(
        "Result bound exceeded: %s produced %d bytes over a %d byte ceiling",
        label,
        size,
        max_bytes,
    )
    return (
        f"{label} produced a {size} byte result, over this server's "
        f"{max_bytes} byte ceiling. Narrow the request — add or tighten a "
        "filter, lower 'limit', or select fewer fields — and call again. The "
        "result was not truncated: a partial payload would look complete."
    )


__all__ = ["enforce_result_bytes"]
