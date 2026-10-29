from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_service_specs import UNSET, UnsetType

from django_spec_mcp.conf import get_setting
from django_spec_mcp.config.types.mcp_config import MCPConfig


def _nullable_int(value: Any) -> int | None:
    """Coerce a resolved bound to ``int``, preserving ``None`` (= disabled)."""
    return None if value is None else int(value)


def _nullable_float(value: Any) -> float | None:
    """Coerce a resolved bound to ``float``, preserving ``None`` (= disabled)."""
    return None if value is None else float(value)


def _given(value: Any, setting: str) -> Any:
    """``value`` when one was passed, else the setting: ``None`` is "not supplied"."""
    return get_setting(setting) if value is None else value


def _bound(value: int | float | None | UnsetType, setting: str) -> Any:
    """Resolve a nullable bound: ``UNSET`` → settings, ``None`` → disabled.

    ``None`` is a *meaningful value* for the outbound bounds ("no ceiling"), so
    it cannot double as the "not supplied" sentinel the other arguments use.
    ``UNSET`` (the kernel's sentinel) carries that distinction:
    ``build_mcp_config()`` takes the setting,
    ``build_mcp_config(dispatch_timeout=None)`` disables the deadline.
    """
    return get_setting(setting) if isinstance(value, UnsetType) else value


def build_mcp_config(
    *,
    protocol_versions: tuple[str, ...] | list[str] | None = None,
    require_protocol_version_header: bool | None = None,
    sessions_enabled: bool | None = None,
    include_structured_content: bool | None = None,
    include_output_schema: bool | None = None,
    allowed_origins: tuple[str, ...] | list[str] | None = None,
    max_request_bytes: int | None = None,
    max_progress_notifications: int | None = None,
    max_result_bytes: int | None | UnsetType = UNSET,
    max_page_size: int | None | UnsetType = UNSET,
    dispatch_timeout: float | None | UnsetType = UNSET,
    page_size: int | None = None,
    include_validation_value: bool | None = None,
    record_service_exceptions: bool | None = None,
    filter_listings_by_permissions: bool | None = None,
    require_tool_permissions: bool | None = None,
    require_tool_descriptions: bool | None = None,
    require_list_pagination: bool | None = None,
    catalog_cache_ttl_ms: int | None = None,
    resource_cache_ttl_ms: int | None = None,
    task_ttl_ms: int | None | UnsetType = UNSET,
    task_poll_interval_ms: int | None | UnsetType = UNSET,
    subscription_max_seconds: float | None | UnsetType = UNSET,
    max_concurrent_subscriptions: int | None | UnsetType = UNSET,
    sse_stream_max_seconds: float | None | UnsetType = UNSET,
    max_concurrent_sse_streams: int | None | UnsetType = UNSET,
    input_request_ttl_seconds: int | None = None,
    max_input_rounds: int | None = None,
) -> MCPConfig:
    """Resolve a ``MCPConfig`` from ``SPEC_MCP``, applying overrides.

    The single place the scalar settings are read.
    [`MCPServer`][django_spec_mcp.server.mcp_server.MCPServer] calls this once in
    ``__init__``; nothing reads these settings per request, which is what lets two
    servers in one project hold different values.

    Every argument is ``None`` by default, meaning "take it from settings". Pass
    one to override just that field for this server:

        MCPServer(name="internal", config=build_mcp_config(page_size=500))

    Use this rather than constructing ``MCPConfig`` directly — it layers
    overrides *over* the project's settings instead of discarding them.
    """
    resolved_versions: tuple[str, ...] = tuple(_given(protocol_versions, "PROTOCOL_VERSIONS"))
    if not resolved_versions:
        # Caught at construction rather than as an ``IndexError`` out of a view
        # on the first request: every version lookup downstream indexes this.
        raise ImproperlyConfigured(
            "SPEC_MCP['PROTOCOL_VERSIONS'] is empty, so this server supports "
            "no MCP revision and cannot answer any request. List at least one."
        )

    return MCPConfig(
        protocol_versions=resolved_versions,
        require_protocol_version_header=bool(
            _given(require_protocol_version_header, "REQUIRE_PROTOCOL_VERSION_HEADER")
        ),
        sessions_enabled=bool(_given(sessions_enabled, "SESSIONS_ENABLED")),
        include_structured_content=bool(
            _given(include_structured_content, "INCLUDE_STRUCTURED_CONTENT")
        ),
        include_output_schema=bool(_given(include_output_schema, "INCLUDE_OUTPUT_SCHEMA")),
        allowed_origins=tuple(_given(allowed_origins, "ALLOWED_ORIGINS")),
        max_request_bytes=int(_given(max_request_bytes, "MAX_REQUEST_BYTES")),
        max_result_bytes=_nullable_int(_bound(max_result_bytes, "MAX_RESULT_BYTES")),
        max_page_size=_nullable_int(_bound(max_page_size, "MAX_PAGE_SIZE")),
        dispatch_timeout=_nullable_float(_bound(dispatch_timeout, "DISPATCH_TIMEOUT")),
        page_size=int(_given(page_size, "PAGE_SIZE")),
        include_validation_value=bool(_given(include_validation_value, "INCLUDE_VALIDATION_VALUE")),
        record_service_exceptions=bool(
            _given(record_service_exceptions, "RECORD_SERVICE_EXCEPTIONS")
        ),
        filter_listings_by_permissions=bool(
            _given(filter_listings_by_permissions, "FILTER_LISTINGS_BY_PERMISSIONS")
        ),
        require_tool_permissions=bool(_given(require_tool_permissions, "REQUIRE_TOOL_PERMISSIONS")),
        require_tool_descriptions=bool(
            _given(require_tool_descriptions, "REQUIRE_TOOL_DESCRIPTIONS")
        ),
        require_list_pagination=bool(_given(require_list_pagination, "REQUIRE_LIST_PAGINATION")),
        max_progress_notifications=int(
            _given(max_progress_notifications, "MAX_PROGRESS_NOTIFICATIONS")
        ),
        catalog_cache_ttl_ms=int(_given(catalog_cache_ttl_ms, "CATALOG_CACHE_TTL_MS")),
        resource_cache_ttl_ms=int(_given(resource_cache_ttl_ms, "RESOURCE_CACHE_TTL_MS")),
        # ``None`` is meaningful for both — "no expiry" and "send no poll hint"
        # — so neither can use ``_given`` as the scalars above do, which would
        # read an explicit ``None`` as "not supplied" and silently pick up the
        # setting instead. ``_bound`` carries
        # that distinction on the sentinel, exactly as the outbound bounds do.
        task_ttl_ms=_nullable_int(_bound(task_ttl_ms, "TASK_TTL_MS")),
        task_poll_interval_ms=_nullable_int(_bound(task_poll_interval_ms, "TASK_POLL_INTERVAL_MS")),
        subscription_max_seconds=_nullable_float(
            _bound(subscription_max_seconds, "SUBSCRIPTION_MAX_SECONDS")
        ),
        max_concurrent_subscriptions=_nullable_int(
            _bound(max_concurrent_subscriptions, "MAX_CONCURRENT_SUBSCRIPTIONS")
        ),
        sse_stream_max_seconds=_nullable_float(
            _bound(sse_stream_max_seconds, "SSE_STREAM_MAX_SECONDS")
        ),
        max_concurrent_sse_streams=_nullable_int(
            _bound(max_concurrent_sse_streams, "MAX_CONCURRENT_SSE_STREAMS")
        ),
        input_request_ttl_seconds=int(
            _given(input_request_ttl_seconds, "INPUT_REQUEST_TTL_SECONDS")
        ),
        max_input_rounds=int(_given(max_input_rounds, "MAX_INPUT_ROUNDS")),
    )


__all__ = ["build_mcp_config"]
