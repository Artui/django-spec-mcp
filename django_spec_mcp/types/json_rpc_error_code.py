"""``JsonRpcErrorCode`` - JSON-RPC 2.0 standard error codes plus MCP-specific reservations."""

from __future__ import annotations

from enum import IntEnum


class JsonRpcErrorCode(IntEnum):
    """JSON-RPC 2.0 standard error codes plus MCP-specific reservations.

    JSON-RPC defines -32700 through -32600 and -32603. MCP then partitions the
    server-error range: ``-32000..-32019`` is implementation-defined,
    ``-32020..-32099`` is reserved for the spec itself, and everything
    allocated here sits in the implementation-defined half — with one
    exception.

    **``-32002`` is not ours to allocate.** The resources spec names it for
    "Resource not found", and the ``2026-07-28`` revision singles it out as the
    one legacy code clients should keep recognising, so a spec-following client
    reads it as a missing resource whatever a server spends it on. It belongs
    to ``RESOURCE_NOT_FOUND`` and nothing else.

    ``-32003`` and ``-32004`` are **burned** rather than reused: they were this
    package's own not-found codes before the wire values were aligned with the
    spec, so an older client still reads them as "resource/prompt not found"
    and "unknown tool". The next implementation-defined code allocated is
    ``-32006``.
    """

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    # Server-defined (MCP):
    SERVER_ERROR = -32000
    UNAUTHORIZED = -32001
    RESOURCE_NOT_FOUND = -32002
    # -32003, -32004: burned — see the class docstring.
    RATE_LIMITED = -32005
    FORBIDDEN = -32006
    # Spec-reserved (``-32020..-32099``), allocated by the MCP specification
    # itself. Not ours to number or to repurpose.
    HEADER_MISMATCH = -32020
    MISSING_REQUIRED_CLIENT_CAPABILITY = -32021
    UNSUPPORTED_PROTOCOL_VERSION = -32022
