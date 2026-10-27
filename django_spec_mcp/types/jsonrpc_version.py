"""``JSONRPC_VERSION`` - the JSON-RPC protocol version this server speaks."""

from __future__ import annotations

JSONRPC_VERSION: str = "2.0"
"""The JSON-RPC protocol version this server speaks. MCP layers on JSON-RPC
2.0; every envelope carries ``"jsonrpc": "2.0"``."""
