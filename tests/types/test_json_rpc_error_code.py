"""``JsonRpcErrorCode``: the standard and MCP-reserved codes."""

from __future__ import annotations

from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode


def test_json_rpc_error_code_standard_set() -> None:
    """Spot-check the standard JSON-RPC + MCP-reserved codes are pinned."""
    assert JsonRpcErrorCode.PARSE_ERROR == -32700
    assert JsonRpcErrorCode.INVALID_PARAMS == -32602
    assert JsonRpcErrorCode.INTERNAL_ERROR == -32603
    assert JsonRpcErrorCode.SERVER_ERROR == -32000
    assert JsonRpcErrorCode.RATE_LIMITED == -32005
