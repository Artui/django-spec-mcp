from __future__ import annotations

from enum import IntEnum


class JsonRpcErrorCode(IntEnum):
    """JSON-RPC 2.0 standard error codes plus MCP-specific reservations."""

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
    HEADER_MISMATCH = -32020
    MISSING_REQUIRED_CLIENT_CAPABILITY = -32021
    UNSUPPORTED_PROTOCOL_VERSION = -32022
